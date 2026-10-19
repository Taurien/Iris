import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from . import brain, status, voice_stt, voice_tts

log = logging.getLogger("iris.ptt")

SAMPLE_RATE = 16000
SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "iris" / "ptt.sock"

# Energy-based voice-activity detection: auto-stop after this much silence
# *following detected speech* (never before any speech, so pausing to think
# right after pressing the hotkey doesn't cut the recording short). RMS
# threshold is on sounddevice's float32 [-1, 1] range — calibrated against
# the user's Creative BT-L3 headset mic (measured: ~0.00003 silence floor,
# ~0.0027 normal speech) — tune if it's triggering on background noise or
# missing quiet speech on a different mic.
SILENCE_THRESHOLD_RMS = 0.0005
SILENCE_DURATION_SEC = 1.5

_stream: sd.InputStream | None = None
_frames: list[np.ndarray] = []
_voice_detected = False
_last_voice_ts = 0.0
_silence_watch_task: asyncio.Task | None = None


def _beep(freq: float, duration: float = 0.12) -> None:
    """Short confirmation tone — played through the default output device
    (never blocks), so there's audible feedback for start/stop without
    needing to look at the desktop widget."""
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    tone = (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    fade = min(200, len(tone) // 4)
    if fade > 0:
        tone[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        tone[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    sd.play(tone, SAMPLE_RATE)


def _audio_callback(indata, _frames_count, _time_info, cb_status):
    global _voice_detected, _last_voice_ts
    if cb_status:
        log.warning("sounddevice status: %s", cb_status)
    _frames.append(indata.copy())
    rms = float(np.sqrt(np.mean(np.square(indata))))
    if rms > SILENCE_THRESHOLD_RMS:
        _voice_detected = True
        _last_voice_ts = time.monotonic()


async def _watch_silence() -> None:
    try:
        while True:
            await asyncio.sleep(0.2)
            if _stream is None:
                return
            if _voice_detected and (time.monotonic() - _last_voice_ts) > SILENCE_DURATION_SEC:
                log.info("auto-stop: %.1fs of silence after speech", SILENCE_DURATION_SEC)
                await _stop_recording_and_respond()
                return
    except asyncio.CancelledError:
        pass


def _start_recording() -> None:
    global _stream, _frames, _voice_detected, _silence_watch_task
    if _stream is not None:
        return
    _frames = []
    _voice_detected = False
    # No `device=` — uses the system's current default input device.
    _stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=_audio_callback)
    _stream.start()
    status.set_state("listening", "Voz local")
    log.info("recording started")
    _beep(880)
    _silence_watch_task = asyncio.create_task(_watch_silence())


async def _stop_recording_and_respond() -> None:
    global _stream
    if _stream is None:
        return
    _stream.stop()
    _stream.close()
    _stream = None
    _beep(440)

    if not _frames:
        status.set_state("idle")
        log.info("no audio captured")
        return
    audio = np.concatenate(_frames, axis=0).reshape(-1)
    log.info("recording stopped, %.1fs captured", len(audio) / SAMPLE_RATE)

    text, lang = await asyncio.to_thread(voice_stt.transcribe_array, audio)
    if not text.strip():
        status.set_state("idle")
        log.info("empty transcription, skipping")
        return

    reply, backend = await brain.ask(text, "Voz local")

    status.set_state("speaking", "Voz local", detail=backend)
    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = Path(tmpdir) / "reply.wav"
        await asyncio.to_thread(voice_tts.synthesize, reply, wav_path, lang)
        await asyncio.to_thread(_play_wav, wav_path)
    if backend is None:
        await asyncio.sleep(status.ERROR_MIN_VISIBLE_SEC)
    status.set_state("idle")


def _play_wav(wav_path: Path) -> None:
    import soundfile as sf

    data, sr = sf.read(str(wav_path), dtype="float32")
    # No `device=` — plays through the system's current default output device.
    sd.play(data, sr)
    sd.wait()


async def _handle_client(reader: asyncio.StreamReader, _writer: asyncio.StreamWriter) -> None:
    line = (await reader.readline()).decode().strip().upper()
    if line == "START":
        _start_recording()
    elif line == "STOP":
        await _stop_recording_and_respond()
    else:
        log.warning("unknown ptt command: %r", line)


async def start_ptt_server() -> asyncio.base_events.Server:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    server = await asyncio.start_unix_server(_handle_client, path=str(SOCKET_PATH))
    log.info("ptt socket listening at %s", SOCKET_PATH)
    return server
