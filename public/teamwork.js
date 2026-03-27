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
    renderMachineApproveList(null);
  } catch {
    try {
      const res = await fetch("./machines.json?v=20260327-1", { cache: "no-store" });
      const data = await res.json();
      machines = data.machines.filter((m) => m.ssh?.enabled);
      isStaticMode = true;
      populateSelect();
      renderMachineApproveList(null);
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

// Per-machine approve
const machineApproveList = document.querySelector("#machineApproveList");

function formatTimeShort(iso) {
  try { return new Date(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" }); }
  catch { return ""; }
}

function renderMachineApproveList(snapshots) {
  const filtered = machines.filter((m) => m.id !== "admira-macmini");
  if (!filtered.length) {
    machineApproveList.innerHTML = '<p class="tw-empty">Sin equipos disponibles.</p>';
    return;
  }

  machineApproveList.innerHTML = filtered.map((m) => {
    return `
    <div class="tw-machine-row" data-id="${m.id}">
      <div class="tw-machine-label">
        <span class="tw-machine-name">${m.name}</span><br>
        <span class="tw-machine-member">${m.member}</span>
      </div>
      <input class="tw-machine-input" data-machine="${m.id}" type="text" placeholder="Prompt para ${m.member}...">
      <select class="tw-approve-sm" data-machine-target="${m.id}" style="background:var(--panel);color:var(--ink);border:1px solid var(--line);padding:8px 6px;font-size:11px;border-radius:10px;">
        <option value="claude">Claude</option>
        <option value="codex">Codex</option>
        <option value="terminal">Terminal</option>
      </select>
      <button class="tw-machine-send" data-machine-send="${m.id}">Enviar</button>
      <button class="tw-approve-sm claude" data-machine="${m.id}" data-target="claude">Aprobar</button>
      <button class="tw-approve-sm codex" data-machine="${m.id}" data-target="codex">Aprobar</button>
    </div>`;
  }).join("");

  machineApproveList.querySelectorAll(".tw-approve-sm[data-machine]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const machineId = btn.dataset.machine;
      const target = btn.dataset.target;
      const origText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "...";

      try {
        const res = await fetch(apiUrl("/api/teamwork/approve-machine"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ machineId, target })
        });
        const data = await res.json();
        btn.textContent = data.ok ? "OK" : "Error";
        setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 2000);
      } catch {
        btn.textContent = "Error";
        setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 2000);
      }
    });
  });

  // Per-machine send prompt
  machineApproveList.querySelectorAll(".tw-machine-send").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const machineId = btn.dataset.machineSend;
      const input = machineApproveList.querySelector(`.tw-machine-input[data-machine="${machineId}"]`);
      const targetSel = machineApproveList.querySelector(`select[data-machine-target="${machineId}"]`);
      const prompt = input?.value.trim();
      if (!prompt) return;

      btn.disabled = true;
      btn.textContent = "...";

      try {
        const res = await fetch(apiUrl("/api/teamwork/send"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ machineId, prompt, target: targetSel?.value || "claude" })
        });
        const data = await res.json();
        btn.textContent = data.ok ? "OK" : "Error";
        if (data.ok) input.value = "";
        setTimeout(() => { btn.textContent = "Enviar"; btn.disabled = false; }, 2000);
        loadHistory();
      } catch {
        btn.textContent = "Error";
        setTimeout(() => { btn.textContent = "Enviar"; btn.disabled = false; }, 2000);
      }
    });
  });

  // Enter to send per-machine
  machineApproveList.querySelectorAll(".tw-machine-input").forEach((input) => {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const machineId = input.dataset.machine;
        machineApproveList.querySelector(`.tw-machine-send[data-machine-send="${machineId}"]`)?.click();
      }
    });
  });
}

// Approve buttons
const approveClaudeBtn = document.querySelector("#approveClaudeBtn");
const approveCodexBtn = document.querySelector("#approveCodexBtn");
const approveClaudeResult = document.querySelector("#approveClaudeResult");
const approveCodexResult = document.querySelector("#approveCodexResult");

async function approveAll(target, btn, resultEl) {
  btn.disabled = true;
  btn.textContent = "Aprobando...";
  resultEl.textContent = "";

  try {
    const res = await fetch(apiUrl("/api/teamwork/approve"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target })
    });
    const data = await res.json();
    const ok = data.results.filter((r) => r.ok).length;
    const fail = data.results.filter((r) => !r.ok).length;
    const names = data.results.map((r) => `${r.machine}: ${r.ok ? "OK" : "error"}`).join(" | ");
    resultEl.textContent = `${ok} aprobados, ${fail} errores — ${names}`;
  } catch (err) {
    resultEl.textContent = `Error: ${err.message}`;
  }

  btn.disabled = false;
  btn.textContent = target === "claude" ? "Aprobar Claude" : "Aprobar Codex";
}

approveClaudeBtn.addEventListener("click", () => approveAll("claude", approveClaudeBtn, approveClaudeResult));
approveCodexBtn.addEventListener("click", () => approveAll("codex", approveCodexBtn, approveCodexResult));

async function loadSnapshots() {
  try {
    const res = await fetch(apiUrl("/api/teamwork/snapshots"), { cache: "no-store" });
    const data = await res.json();
    if (data.ok) {
      renderMachineApproveList(data.snapshots);
    }
  } catch {
    // silently fail
  }
}

loadMachines();
loadHistory();
setTimeout(loadSnapshots, 2000);
setInterval(loadHistory, 10_000);
setInterval(loadSnapshots, 60_000);
