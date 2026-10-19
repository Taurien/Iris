(function () {
  const STATE_LABEL = {
    idle: "en espera",
    listening: "escuchando…",
    thinking: "pensando…",
    speaking: "hablando…",
  };

  // signal-orb.js only knows listening/thinking/searching/done — map our
  // states onto the closest visual. "speaking" has no direct equivalent, so
  // it reuses "thinking" (an active/pulsing shape) rather than "searching",
  // which reads more like "looking something up".
  const ORB_STATE = {
    idle: "done",
    listening: "listening",
    thinking: "thinking",
    speaking: "thinking",
  };

  const orb = document.getElementById("orb");
  const stateLabel = document.getElementById("state-label");
  const messages = document.getElementById("messages");
  const composer = document.getElementById("composer");
  const input = document.getElementById("input");
  const sendBtn = document.getElementById("send");

  let lastState = "idle";

  function addBubble(text, who) {
    const div = document.createElement("div");
    div.className = "bubble bubble-" + who;
    div.textContent = text;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  async function pollStatus() {
    try {
      const res = await fetch("/status", { cache: "no-store" });
      const data = await res.json();
      if (data.state !== lastState) {
        orb.setAttribute("state", ORB_STATE[data.state] || "done");
        stateLabel.textContent = STATE_LABEL[data.state] || data.state;
        lastState = data.state;
      }
    } catch (e) {
      // offline / server down — leave last known state showing
    }
  }

  async function sendMessage(text) {
    addBubble(text, "user");
    const pending = addBubble("…", "iris");
    pending.classList.add("bubble-pending");
    sendBtn.disabled = true;

    try {
      const res = await fetch("/message", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": "Bearer " + (window.IRIS_CONFIG && window.IRIS_CONFIG.apiKey),
        },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      pending.textContent = data.reply || data.error || "(sin respuesta)";
      pending.classList.remove("bubble-pending");
    } catch (e) {
      pending.textContent = "No pude conectar con Iris.";
      pending.classList.remove("bubble-pending");
    } finally {
      sendBtn.disabled = false;
    }
  }

  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    sendMessage(text);
  });

  if (window.Telegram && window.Telegram.WebApp) {
    window.Telegram.WebApp.ready();
    window.Telegram.WebApp.expand();
  }

  pollStatus();
  setInterval(pollStatus, 1000);
})();
