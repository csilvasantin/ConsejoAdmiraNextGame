const quickInput = document.querySelector("#quickInput");
const machineSelect = document.querySelector("#machineSelect");
const targetSelect = document.querySelector("#targetSelect");
const promptArea = document.querySelector("#promptArea");
const sendBtn = document.querySelector("#sendBtn");
const feedback = document.querySelector("#feedback");
const historyList = document.querySelector("#historyList");

let machines = [];
let isStaticMode = false;
const FUNNEL_URL = "https://macmini.tail48b61c.ts.net";
const isRemote = location.hostname !== "localhost" && location.hostname !== "127.0.0.1";

function apiUrl(path) {
  return isRemote ? `${FUNNEL_URL}${path}` : path;
}

function showFeedback(text, ok) {
  feedback.textContent = text;
  feedback.className = "tw-feedback " + (ok ? "ok" : "err");
  setTimeout(() => { feedback.className = "tw-feedback"; }, 4000);
}

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return iso;
  }
}

function resolveName(input) {
  const q = input.toLowerCase().replace(/[\s\-_]+/g, "");
  return machines.find((m) => {
    const id = m.id.toLowerCase().replace(/[\s\-_]+/g, "");
    const name = m.name.toLowerCase().replace(/[\s\-_]+/g, "");
    return id.includes(q) || name.includes(q) || id.replace("admira", "").includes(q);
  }) || null;
}

function parseQuickInput(text) {
  const trimmed = text.trim();
  if (!trimmed) return null;

  for (const m of machines) {
    const names = [
      m.id,
      m.id.replace("admira-", ""),
      m.name
    ];
    for (const alias of names) {
      if (trimmed.toLowerCase().startsWith(alias.toLowerCase())) {
        const rest = trimmed.slice(alias.length).trim();
        if (rest) return { machineId: m.id, prompt: rest };
      }
    }
  }

  const parts = trimmed.split(/\s+/);
  const first = parts[0];
  const resolved = resolveName(first);
  if (resolved && parts.length > 1) {
    return { machineId: resolved.id, prompt: parts.slice(1).join(" ") };
  }

  return null;
}

async function send(machineId, prompt, target) {
  sendBtn.disabled = true;
  sendBtn.textContent = "Enviando...";

  try {
    const res = await fetch(apiUrl("/api/teamwork/send"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ machineId, prompt, target })
    });
    const data = await res.json();

    if (data.ok) {
      showFeedback(`Enviado a ${data.name || machineId}`, true);
      quickInput.value = "";
      promptArea.value = "";
    } else {
      showFeedback(`Error: ${data.error}`, false);
    }
  } catch (err) {
    showFeedback(`Error de conexión: ${err.message}`, false);
  }

  sendBtn.disabled = false;
  sendBtn.textContent = "Enviar";
  loadHistory();
}

function handleQuickSend() {
  const parsed = parseQuickInput(quickInput.value);
  if (parsed) {
    send(parsed.machineId, parsed.prompt, targetSelect.value);
  } else {
    showFeedback("Formato: NombreMáquina texto del prompt", false);
  }
}

function handleFormSend() {
  const machineId = machineSelect.value;
  const prompt = promptArea.value.trim();
  const target = targetSelect.value;
  if (!machineId || !prompt) {
    showFeedback("Selecciona máquina y escribe un prompt", false);
    return;
  }
  send(machineId, prompt, target);
}

function renderHistory(entries) {
  if (!entries.length) {
    historyList.innerHTML = '<p class="tw-empty">Sin comandos enviados todavía.</p>';
    return;
  }

  historyList.innerHTML = entries.map((e) => {
    const captureHtml = e.captureId
      ? `<div class="tw-terminal" id="capture-${e.captureId}"><span class="tw-terminal-loading">Capturando terminal...</span></div>`
      : "";
    return `
      <div class="tw-entry">
        <div class="tw-entry-header">
          <span class="tw-entry-machine">${e.machineName} <span class="tw-entry-target">${e.target || "terminal"}</span><span class="tw-entry-status ${e.status}"></span></span>
          <span class="tw-entry-prompt">${e.prompt}</span>
          <span class="tw-entry-time">${formatTime(e.sentAt)}</span>
        </div>
        ${captureHtml}
      </div>`;
  }).join("");

  // Load terminal captures
  for (const e of entries) {
    if (e.captureId) loadCapture(e.captureId);
  }
}

async function loadCapture(captureId) {
  const el = document.querySelector(`#capture-${captureId}`);
  if (!el || el.dataset.loaded === "true") return;

  try {
    const res = await fetch(apiUrl(`/api/teamwork/capture/${captureId}`), { cache: "no-store" });
    const data = await res.json();
    if (data.ok) {
      if (data.type === "image") {
        el.className = "tw-screenshot";
        el.innerHTML = `<img src="${apiUrl(data.path)}" alt="Captura de pantalla" loading="lazy">`;
      } else if (data.type === "text") {
        el.className = "tw-terminal";
        el.innerHTML = `<pre>${data.text.replace(/</g, "&lt;")}</pre>`;
      }
      el.dataset.loaded = "true";
    }
  } catch {
    // will retry on next poll
  }
}

async function loadHistory() {
  try {
    const res = await fetch(apiUrl("/api/teamwork/history"), { cache: "no-store" });
    const data = await res.json();
    renderHistory(data.entries || []);
  } catch {
    // silently fail
  }
}

function populateSelect() {
  machineSelect.innerHTML = machines.map((m) =>
    `<option value="${m.id}">${m.name} (${m.member})</option>`
  ).join("");
}

async function loadMachines() {
  try {
    const res = await fetch(apiUrl("/api/machines"), { cache: "no-store" });
    if (!res.ok) throw new Error("api unavailable");
    const data = await res.json();
    machines = data.machines.filter((m) => m.ssh?.enabled);
    isStaticMode = false;
    populateSelect();
  } catch {
    try {
      const res = await fetch("./machines.json?v=20260327-1", { cache: "no-store" });
      const data = await res.json();
      machines = data.machines.filter((m) => m.ssh?.enabled);
      isStaticMode = true;
      populateSelect();
      sendBtn.textContent = "Solo lectura";
      sendBtn.disabled = true;
    } catch {
      machineSelect.innerHTML = '<option value="">Sin conexión</option>';
    }
  }
}

quickInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    handleQuickSend();
  }
});

sendBtn.addEventListener("click", () => {
  if (quickInput.value.trim()) {
    handleQuickSend();
  } else {
    handleFormSend();
  }
});

loadMachines();
loadHistory();
setInterval(loadHistory, 10_000);
