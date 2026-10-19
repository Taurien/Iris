# Iris — asistente personal (fase 1: esta máquina)

Software para la fase 1 del proyecto `asistente` (ver `../README.md` para las notas de hardware de la fase 2, el Raspberry Pi futuro, y la decisión del nombre). Esta carpeta es el código que eventualmente migra a / se conecta con ese dispositivo.

## Qué es

Un asistente personal propio, que corre siempre activo en esta máquina (systemd --user, mismo patrón que `mcp-memory.service`), accesible por:
- **Telegram**, cuando estás fuera de casa.
- **Voz local**, con el micrófono/parlantes que estén conectados (push-to-talk), cuando estás en casa.

Ambos canales comparten **un solo hilo de conversación** (mismo `session_id`), etiquetado por canal de origen en cada mensaje.

## Por qué así (y no con un framework de terceros)

Investigamos **Hermes Agent** (NousResearch) y **Honcho** (Plastic Labs), mencionados en un reel sobre cómo armar un "Jarvis" con 9 piezas. Conclusión:

- **Hermes Agent** tiene voz nativa excelente (STT/TTS/wake-word locales, hasta trae "jarvis" como palabra de activación) y se conecta trivialmente a nuestro `mcp-memory-service` vía MCP — pero su propio issue tracker (a la fecha) muestra fugas de memoria/fd y caídas silenciosas del conector de Telegram exactamente en el patrón "gateway siempre corriendo" que necesitamos. No apto para dejarlo desatendido todavía.
- **Honcho** es redundante con lo que ya construimos en `mcp-memory-service` (búsqueda semántica, grafo de entidades, scoring de calidad, detección de contradicciones).
- Tampoco encontramos un framework de terceros (nanobot, QwenPaw, majordomo) que combine madurez real + superficie mínima de ataque para este caso de uso puntual.

Por eso: **construir algo propio y mínimo**, del tamaño justo para lo que hace falta — controlable de punta a punta, sin heredar la superficie de ataque de un framework grande.

## Arquitectura

### Cerebro intercambiable, con rotación automática

No depende de un solo LLM/agente. Usa cualquiera de los 3 CLIs ya pagos en esta máquina, en modo headless. `IRIS_BRAIN=auto` (default) rota sola según el mensaje y hace fallback si un backend falla:

- **Mensajes generales** → `antigravity` (Google, plan Google AI Pro) primero → `claude-code` → `opencode`.
- **Mensajes relacionados a código** (`iris/brain/classify.py`, heurística local por palabras clave/patrones — sin llamada extra a ningún LLM) → `claude-code` primero → `opencode` → `antigravity`.
- Si un backend falla (límite de uso agotado, caído, lo que sea) pasa al siguiente de la cadena automáticamente — nunca revienta, en el peor caso devuelve un mensaje de disculpa.
- Cada backend guarda **su propio `session_id`** (`iris/sessions.py`, uno por backend, no uno global) — un fallback no intenta adivinar un session id ajeno, arranca fresco para ese backend puntual.
- Fijar `IRIS_BRAIN` a un valor específico (no `auto`) desactiva la rotación y fuerza ese backend siempre — útil para debuggear.

| Backend | Comando base | Restricción de herramientas |
|---|---|---|
| `claude-code` | `claude -p "<texto>" --output-format json --session-id/--resume ... --mcp-config config/mcp.json --strict-mcp-config --settings config/settings.json --tools "" --allowedTools "mcp__memory-service__*"` | `--tools ""` + MCP scoping — nativo de Claude Code |
| `antigravity` | `agy -p "<texto>" --output-format json --continue/-c --conversation <id> --sandbox --agent iris --dangerously-skip-permissions` | Agente custom `.agents/agents/iris.md` con `tools: []` + `commandExecutionPolicy: off` + `mcpServers` scopeado a memory-service. **Verificado empíricamente**: con `tools: []`, ni archivos ni terminal quedan disponibles aunque se use `--dangerously-skip-permissions` (necesario porque el modo headless no tiene a quién pedirle aprobación interactiva del tool de MCP, y `--mode auto` no está disponible en esta cuenta) — probado pidiéndole leer `.env` y seguía negándose. |
| `opencode` | `opencode run "<texto>" --format json --session <id> --continue --agent iris --dir <project>` | Agente custom `iris/opencode.json` (`agent.iris`) con `permission` denegando bash/edit/write/read/glob/grep/list/patch/webfetch/websearch/task/skill/lsp, y los MCP servers globales (`codebase-memory-mcp`, `playwright`, `chrome-devtools`) deshabilitados a nivel de proyecto — solo `memory-service` queda habilitado. **⚠️ Bloqueado, no funcional todavía**: ver nota abajo. |

Ninguno usa la API paga por separado — los tres corren bajo las suscripciones/logins ya interactivos de esta máquina (Claude Pro/Max, Google AI Pro, lo que tenga logueado opencode).

**⚠️ opencode no puede usarse como respaldo hasta que tenga login.** `opencode providers list` muestra `0 credentials` — nunca se autenticó con ningún proveedor real, y los modelos free (`opencode/*-free`) devuelven `403 FreeTierError: "OpenCode's free tier can only be used from within OpenCode"` en modo headless (`opencode run`), solo funcionan desde su TUI interactiva. Resultado: ahora mismo, cada vez que la cadena de rotación llegue a `opencode`, va a fallar — no es un problema del agente restringido (`iris/opencode.json`, ya armado y lógicamente correcto), es que no hay ninguna cuenta real detrás. De paso se corrigió un bug independiente: el modelo default global (`~/.config/opencode/opencode.json`) apuntaba a `opencode/mimo-v2.5-free`, que ya no existe (renombrado a `mimo-v2.6-flash-free` río arriba) — corregido en ese archivo y fijado explícitamente en `iris/opencode.json`. **Pendiente**: correr `opencode providers login` con una cuenta real (GitHub Copilot, o lo que decida el usuario) antes de que este backend sirva de algo.

### Memoria

Reutiliza `mcp-memory-service` ya construido (http://127.0.0.1:8000/mcp) — sin stack nuevo. `CLAUDE.md` le instruye explícitamente a Iris que busque/guarde ahí proactivamente (no depende del hook global de `~/.claude/settings.json`, porque `--settings config/settings.json` lo reemplaza por el perfil restringido propio).

### Voz — bilingüe (español latam / inglés)

- **STT**: `faster-whisper`, modelo `small`, con `language=None` para auto-detectar entre español e inglés en cada mensaje (mismo patrón de decodificación ya probado en esta sesión: WAV a numpy directamente, sin pasar por la librería `av`, que crashea por incompatibilidad de versión en esta máquina).
- **TTS**: `Piper` (`piper-tts`, fork mantenido `OHF-Voice/piper1-gpl`), dos voces en `models/piper/` elegidas según el idioma detectado — `es_MX-ald-medium` para español, `en_US-lessac-high` para inglés. Rápido en CPU, corre también en Raspberry Pi 5 (relevante para la fase 2). Nota: `synthesize_wav()` necesita un `wave.Wave_write` real (`wave.open(path, "wb")`), no un file handle crudo.
  - Se probó primero `es_MX-claude-high`, pero esa voz puntual tenía un glitch de pronunciación reproducible en "Iris" (decía "Giris"/"Diris"/variantes, consonante espuria distinta cada vez) — artefacto del vocoder de esa voz, no de Piper en general. Se resolvió cambiando a `es_MX-ald-medium`.
- `CLAUDE.md` le indica a Iris responder siempre en el mismo idioma en que le hablan (sin mezclar), en español latam neutro cuando corresponde.
- Audio local usa siempre el dispositivo de entrada/salida **default del sistema** (nunca hardcodeado a un auricular puntual).

### Entorno: GNOME + Wayland

Las librerías Python de hotkey global (`keyboard`, `pynput` global) no funcionan de forma confiable en Wayland. Solución: un atajo de teclado configurado en GNOME (una vez, manual) que invoca un cliente CLI chiquito que habla por socket Unix con el daemon — no se intenta capturar la tecla desde Python.

### Presencia — que Iris esté "viva", no solo responda cuando le escribís

Inspirado en [Coucou](https://github.com/louis-cfm/coucou) (ver análisis en el historial de la sesión): un companion visual que muestra en vivo qué está haciendo el agente. Se adaptó la idea, no el stack (Coucou es Swift/Tauri nativo; acá todo sigue siendo Python + web, consistente con el resto de Iris):

- **`iris/status.py`** — estado en memoria (`idle` / `listening` / `thinking` / `speaking` + canal activo), actualizado por `brain.ask()`, `telegram_bot.py` y `ptt_daemon.py` en cada transición.
- **`iris/web.py`** — API HTTP local (`127.0.0.1:8787`, nunca expuesta directo a internet):
  - `GET /status` — el estado de arriba, sin auth (no es sensible).
  - `POST /message {"text": "..."}` — chatear con Iris por HTTP directo (comparte el mismo `session_id`/memoria que Telegram y la voz local). Requiere `Authorization: Bearer <IRIS_API_KEY>` (`.env`, generado una vez).
  - `GET /app/*` — sirve la mini app web (`webapp/`).
- **Widget de escritorio** (`iris/widget/`) — ventana GTK4 chiquita, siempre visible, con un punto de color por estado + texto. Standalone: corre con el Python del sistema (no el venv del proyecto, para no depender de empaquetar PyGObject ahí) y solo habla con Iris por HTTP (`/status`), nunca importa el paquete `iris` directamente.
  - GNOME Wayland no soporta `wlr-layer-shell` y GTK4 sacó `set_keep_above()`/`set_skip_taskbar_hint()` de la API — mismo límite que ya documentó Coucou en este mismo entorno. Solución igual a la de ellos: forzar XWayland (`GDK_BACKEND=x11`, en `widget/launch.sh`) y usar `wmctrl` para fijar "siempre arriba" + posición, una vez que la ventana se muestra.
  - Arranca solo al iniciar sesión vía `~/.config/autostart/iris-widget.desktop`.
- **Mini app web** (`webapp/`) — chat con burbujas + un **orb animado** (`signal-orb.js`, de [Ship Notes](https://github.com/aqualang89/shipnotes-components), MIT, ver `webapp/THIRD_PARTY_NOTICES.md`) en vez de un punto de color plano — web component standalone, WebGL con fallback a Canvas2D, un solo archivo sin dependencias. Mapeo de estados: `idle→done`, `listening→listening`, `thinking→thinking`, `speaking→thinking` (el orb no tiene un estado "speaking" nativo, se reusa "thinking" por ser la forma más parecida a "activo/emitiendo"). Pensada para abrirse como [Telegram Mini App](https://core.telegram.org/bots/webapps) dentro del propio bot (así tenés "interfaz" en el celular, no solo chat plano). Usa la misma clave (`IRIS_API_KEY`) que la API — se sirve dinámicamente vía `/app/config.js`, nunca queda hardcodeada en un archivo estático. **Limitación conocida**: la clave viaja a cualquiera que cargue la página — la protección real hoy es que la URL del túnel no es adivinable, no autenticación real; si en algún momento importa más, el camino correcto es verificar el `initData` que Telegram firma con el token del bot.
  - **Bug encontrado y arreglado**: `/app/` (la ruta exacta que usa el botón de menú de Telegram) devolvía 403 — aiohttp's `add_static` con `show_index=False` no cae a `index.html` solo para una request de directorio, no es un fallback automático como en otros servidores. Agregado `handle_webapp_index` con una ruta explícita `GET /app/` antes del mount estático (`web.py`).
  - **URL permanente: `https://iris.michelcruz.me`**. Dominio propio del usuario (Namecheap), DNS migrado a Cloudflare (plan Free, "Connect" — no transfer, el registrador sigue siendo Namecheap) específicamente para esto. Tunnel nombrado de `cloudflared` (`systemd/iris-tunnel.service`, config en `cloudflared-config.yml`, credenciales en `~/.cloudflared/<tunnel-id>.json`) en vez del túnel rápido efímero que se usaba antes — la URL ya no cambia al reiniciar el servicio o la máquina. El botón de menú del bot (`setChatMenuButton`) y el default de la app de Android ya apuntan acá. Checklist completo de la migración de DNS (los 7 registros originales de Namecheap preservados 1:1) en `../dns-migration-checklist-michelcruz.md`.

## Etapas de construcción

1. **Texto por Telegram** → cerebro headless → respuesta. **Probado con mensajes reales del usuario, funcionando.**
2. **Voz por Telegram** — notas de voz entrantes se transcriben (faster-whisper) y las respuestas se sintetizan (Piper) y se mandan como nota de voz. **Pipeline de audio probado end-to-end (texto→voz→OGG→voz→texto idéntico); el servicio completo ya corre vía systemd.**
3. **Voz local, push-to-talk** — mismo STT/TTS, disparado por el atajo de GNOME (`iris.ptt_client toggle`, `bash -c 'cd ... && .venv/bin/python3 -m iris.ptt_client toggle'` como comando del atajo). **Probado end-to-end con mic real.** Mejoras sobre el push-to-talk puro original:
   - **Auto-stop por silencio (VAD)**: detección de voz por energía (RMS) en `ptt_daemon.py` — corta sola a los 1.5s de silencio después de detectar que hubo habla (nunca antes, así pausar para pensar justo después de apretar el atajo no corta de más). Umbral calibrado contra el mic real del usuario (headset Creative Zen Hybrid Pro vía dongle BT-L3) — puede necesitar recalibración si cambia de mic (medir RMS en silencio vs. hablando y poner el umbral entre medio).
   - **Beeps de confirmación**: un tono corto (880Hz) al empezar a grabar y otro (440Hz) al parar — para no depender de mirar el widget para saber si está escuchando.
   - El atajo sigue sirviendo para cortar manual en cualquier momento (override), no solo para arrancar.
   - Wake-word (manos libres, sin apretar nada) sigue fuera de alcance.
4. **Presencia (widget de escritorio + mini app web)** — ver sección arriba. **Widget probado (levanta sin errores), mini app servida por curl y expuesta vía túnel HTTPS real, registrada como botón de menú del bot.**

## Seguridad

Esta es la razón de fondo para construir esto propio:

- `allowlist.is_allowed(user_id)` es lo primero que corre en cada handler de Telegram — remitentes no autorizados se loguean y se ignoran en silencio, sin confirmar que el bot esté "vivo".
- El cerebro corre con un perfil **deliberadamente más restringido** que el uso interactivo normal, en los 3 backends: sin Bash/Edit/Write/Read/WebFetch/Task, con acceso *solo* a las tools de `memory-service` vía MCP — confirmado empíricamente en Claude Code (`permission_denials: []`) y en Antigravity (pedirle leer `.env` o correr `whoami` se niega, incluso con `--dangerously-skip-permissions`, porque `tools: []` en el agente custom remueve las tools a nivel estructural, no solo las bloquea detrás de un prompt).
- Secretos (token de Telegram, etc.) solo en `.env` (gitignored, `chmod 600`), nunca hardcodeados.
- Corre como usuario normal, sin sudo/root, sin acceso a dispositivos de input crudos.
- Log local del costo por turno (confirmado: ~$0.15 el primer turno de una sesión por el cache creation del MCP, ~$0.008 los siguientes por cache read).

## Estructura

```
iris/
├── CLAUDE.md               # persona de Iris + aclara que no tiene Bash/Edit en este canal
├── .env                    # secretos (gitignored)
├── pyproject.toml
├── config/
│   ├── mcp.json             # solo memory-service
│   └── settings.json        # segunda capa de deny explícito
├── .agents/agents/iris.md    # agente custom restringido para Antigravity
├── iris/
│   ├── main.py               # entrypoint (telegram + ptt + web, los 3 juntos)
│   ├── brain/                # cerebro con rotación (claude_code / opencode / antigravity)
│   │   └── classify.py        # heurística código-vs-general para elegir la cadena
│   ├── sessions.py           # un session_id por backend
│   ├── status.py             # estado en vivo (idle/listening/thinking/speaking)
│   ├── web.py                # API HTTP local (status, chat, mini app)
│   ├── allowlist.py
│   ├── telegram_bot.py
│   ├── voice_stt.py
│   ├── voice_tts.py
│   ├── audio_convert.py
│   ├── ptt_daemon.py
│   ├── ptt_client.py
│   └── widget/                # desktop widget standalone (Python del sistema, no el venv)
│       ├── main.py
│       └── launch.sh
├── webapp/                    # mini app web (sirve desde web.py en /app/)
│   ├── index.html
│   ├── style.css
│   └── app.js
├── state/sessions.json      # gitignored (uno por backend)
├── state/tunnel.log          # gitignored
├── models/piper/             # voces .onnx, gitignored
└── systemd/
    ├── iris.service           # el daemon (telegram + voz local + API)
    └── iris-tunnel.service     # túnel cloudflared para la mini app/Android
```

(La app de Android vive aparte, en `../android/` — ver `../android/SPEC.md`.)

## Cómo levantarlo

```sh
# 1. Completar .env: TELEGRAM_BOT_TOKEN (de @BotFather) y ALLOWED_TELEGRAM_USER_ID (de @userinfobot)
# 2. Instalar las dependencias de sistema (una vez):
sudo apt-get install -y libportaudio2 wmctrl
# 3. Probar a mano antes de systemd:
cd /home/taurien_l/Desktop/Lab/asistente/iris
uv run python -m iris.main
# 4. Si anda, activar como servicios siempre-encendidos:
mkdir -p ~/.config/systemd/user
cp systemd/iris.service systemd/iris-tunnel.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now iris.service iris-tunnel.service
# 5. El widget de escritorio arranca solo en el próximo login (ya está en
#    ~/.config/autostart/). Para probarlo ahora mismo sin reiniciar sesión:
iris/widget/launch.sh
# 6. URL pública: https://iris.michelcruz.me (permanente, no cambia con reinicios)
```

## Estado

Nombre: **Iris**. Las 4 etapas + rotación de cerebro + presencia (widget/mini app) completas y **corriendo de verdad** vía systemd (`iris.service` + `iris-tunnel.service`, ambos `enabled`, sobreviven logout por el linger ya activado en esta máquina). Telegram probado con mensajes reales del usuario. Botón de menú del bot y default de la app de Android ya apuntan a la URL permanente `https://iris.michelcruz.me`. Push-to-talk local con atajo de teclado de GNOME (`iris.ptt_client toggle`) configurado y probado end-to-end. App de Android construida (ver `../android/SPEC.md`) — pendiente de probar en un dispositivo real con el nuevo default (la APK ya compilada tiene el viejo `127.0.0.1`, hay que recompilar o escribir la URL a mano en Ajustes).

Pendiente: recompilar/reinstalar la app de Android para que tome el nuevo default de URL; login real de `opencode` (`opencode providers login`) — el agente restringido ya está armado pero el backend no sirve hasta que tenga una cuenta autenticada, ver nota en la sección de cerebro.
