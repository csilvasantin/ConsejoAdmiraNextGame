const quickInput = document.querySelector("#quickInput");
const sendAllBtn = document.querySelector("#sendAllBtn");
const onboardingAllBtn = document.querySelector("#onboardingAllBtn");
const feedback = document.querySelector("#feedback");
const historyList = document.querySelector("#historyList");

let machines = [];
let isStaticMode = true;
const FUNNEL_URL = "";
const FUNNEL_HOST = "";
const isLocal = false;
const DEFAULT_ONBOARDING_PROMPT = "";
const LOCAL_ONBOARDING_COMMANDS = new Set();
const GLOBAL_ONBOARDING_COMMANDS = new Set();
const GROUP_LABELS = {
  council: "Consejo de Administracion",
  worker: "Equipo"
};

// Static mode — no redirect, no Funnel
function apiUrl(path) {
  return path;
}

function normalizeCommand(text) {
  return text.trim().toLowerCase().replace(/\s+/g, " ");
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

const sendAllTarget = document.querySelector("#sendAllTarget");

async function sendToAll(prompt) {
  sendAllBtn.disabled = true;
  sendAllBtn.textContent = "Enviando...";
  const target = sendAllTarget.value;

  try {
    const res = await fetch(apiUrl("/api/teamwork/send-all"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, target })
    });
    const data = await res.json();
    const ok = data.results.filter((r) => r.ok).length;
    const label = target === "all" ? "Claude + Codex" : target.charAt(0).toUpperCase() + target.slice(1);
    showFeedback(`Enviado a ${ok} destinos (${label} en todos los equipos)`, true);
    quickInput.value = "";
  } catch (err) {
    showFeedback(`Error: ${err.message}`, false);
  }

  sendAllBtn.disabled = false;
  sendAllBtn.textContent = "Enviar a todos";
  loadHistory();
}

async function sendOnboardingAll(prompt = DEFAULT_ONBOARDING_PROMPT) {
  onboardingAllBtn.disabled = true;
  sendAllBtn.disabled = true;
  onboardingAllBtn.textContent = "Lanzando...";

  try {
    const res = await fetch(apiUrl("/api/teamwork/onboarding-all"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt })
    });
    const data = await res.json();
    const ok = data.results.filter((r) => r.ok).length;
    const offline = data.results.filter((r) => r.skipped).length;
    const fail = data.results.filter((r) => !r.ok && !r.skipped).length;
    const parts = [`${ok} equipos actualizados`];
    if (offline) parts.push(`${offline} offline`);
    if (fail) parts.push(`${fail} con error`);
    showFeedback(`Onboarding all lanzado: ${parts.join(" | ")}`, ok > 0);
    quickInput.value = "";
  } catch (err) {
    showFeedback(`Error: ${err.message}`, false);
  }

  onboardingAllBtn.disabled = false;
  sendAllBtn.disabled = false;
  onboardingAllBtn.textContent = "Onboarding all";
  loadHistory();
}

async function handleQuickCommand(prompt) {
  const normalized = normalizeCommand(prompt);

  if (LOCAL_ONBOARDING_COMMANDS.has(normalized)) {
    showFeedback("`onboarding` es local: hazlo en esta sesion. Usa `onboarding all` si quieres refrescar todo AdmiraNext.", false);
    return true;
  }

  if (GLOBAL_ONBOARDING_COMMANDS.has(normalized)) {
    await sendOnboardingAll();
    return true;
  }

  return false;
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

async function loadMachines() {
  try {
      const res = await fetch("./machines.json?v=20260331-4", { cache: "no-store" });
    const data = await res.json();
    machines = data.machines;
    isStaticMode = true;
    renderMachineApproveList(null);
    if (sendAllBtn) { sendAllBtn.textContent = "Solo lectura"; sendAllBtn.disabled = true; }
    if (onboardingAllBtn) { onboardingAllBtn.textContent = "Solo lectura"; onboardingAllBtn.disabled = true; }
  } catch {
    // no machines
  }
}

// Per-machine approve
const machineApproveList = document.querySelector("#machineApproveList");

function formatTimeShort(iso) {
  try { return new Date(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" }); }
  catch { return ""; }
}

function renderMachineApproveList(snapshots) {
  const filtered = machines;
  if (!filtered.length) {
    machineApproveList.innerHTML = '<p class="tw-empty">Sin equipos disponibles.</p>';
    return;
  }

  const sorted = [...filtered].sort((a, b) => {
    const aGroup = (a.unitType || "council") === "worker" ? 1 : 0;
    const bGroup = (b.unitType || "council") === "worker" ? 1 : 0;
    if (aGroup !== bGroup) {
      return aGroup - bGroup;
    }
    const aOnline = snapshots?.[a.id] ? 1 : 0;
    const bOnline = snapshots?.[b.id] ? 1 : 0;
    return bOnline - aOnline;
  });

  let currentGroup = null;
  machineApproveList.innerHTML = sorted.map((m) => {
    const group = m.unitType || "council";
    const snap = snapshots?.[m.id];
    const remoteReady = !isStaticMode && Boolean(m.ssh?.enabled || m.automation?.enabled);
    let monitorContent;
    const multiLabels = ["Claude", "Studio", "Codex"];
    if (snap && snap.type === "images") {
      const t = Date.now();
      const orients = snap.orientations || snap.images.map(() => "portrait");
      monitorContent = `<div class="tw-multi-monitor">${snap.images.map((imgPath, i) => {
        const src = (imgPath.startsWith("/") ? apiUrl(imgPath) : imgPath) + `?t=${t}`;
        return `<div class="tw-multi-screen ${orients[i]}"><img src="${src}" alt="${multiLabels[i]}"><span class="tw-screen-label">${multiLabels[i]}</span></div>`;
      }).join("")}</div><span class="tw-machine-monitor-time">${formatTimeShort(snap.updatedAt)}</span>`;
    } else if (snap && snap.type === "image") {
      const imgSrc = snap.image.startsWith("/") ? apiUrl(snap.image) : snap.image;
      const cacheBust = imgSrc.includes("?") ? `&t=${Date.now()}` : `?t=${Date.now()}`;
      monitorContent = `<img src="${imgSrc}${cacheBust}" alt="${m.name}" style="width:100%;height:100%;object-fit:cover;border-radius:6px;"><span class="tw-machine-monitor-time">${formatTimeShort(snap.updatedAt)}</span>`;
    } else if (snap && snap.text) {
      monitorContent = `<pre>${snap.text.replace(/</g, "&lt;")}</pre><span class="tw-machine-monitor-time">${formatTimeShort(snap.updatedAt)}</span>`;
    } else {
      monitorContent = `<div class="tw-machine-monitor-empty">Sin señal</div>`;
    }
    const intro = group !== currentGroup ? `<div class="tw-group-title tw-group-${group}">${GROUP_LABELS[group] || group}</div>` : "";
    currentGroup = group;
    return `${intro}
    <div class="tw-machine-row" data-id="${m.id}">
      <div class="tw-machine-monitor small" data-monitor="${m.id}">${monitorContent}</div>
      <div class="tw-machine-label">
        <span class="tw-machine-name">${m.name}</span><br>
        <span class="tw-machine-member">${m.member} · ${m.platform}</span>
        ${m.unitType === "worker" ? `<div class="tw-machine-caps"><span class="tw-machine-cap tw-machine-cap-kind">PC</span>${(m.capabilities || []).map((cap) => `<span class="tw-machine-cap">${cap}</span>`).join("")}</div>` : ""}
        <span class="tw-app-status">
          ${snap?.claudeState ? `<span class="tw-app-tag claude" title="Claude: ${snap.claudeState}">C</span>` : ""}
          ${snap?.codexState ? `<span class="tw-app-tag codex" title="Codex: ${snap.codexState}">X</span>` : ""}
        </span>
      </div>
      <input class="tw-machine-input" data-machine="${m.id}" type="text" placeholder="Prompt para ${m.member}..." ${remoteReady ? "" : "disabled"}>
      <select class="tw-approve-sm" data-machine-target="${m.id}" style="background:var(--panel);color:var(--ink);border:1px solid var(--line);padding:8px 6px;font-size:11px;border-radius:10px;">
        <option value="claude">Claude</option>
        <option value="codex">Codex</option>
        <option value="terminal">Terminal</option>
      </select>
      <button class="tw-machine-send" data-machine-send="${m.id}" ${remoteReady ? "" : "disabled"}>${remoteReady ? "Enviar" : "Pendiente"}</button>
      <button class="tw-machine-approve" data-machine-approve="${m.id}" ${remoteReady ? "" : "disabled"}>${remoteReady ? "Aprobar" : "Sin canal"}</button>
      <span class="tw-auto-badge ${remoteReady ? "" : "tw-auto-badge-off"}" data-watchdog-machine="${m.id}">${remoteReady ? "🤖 0" : "offline"}</span>
    </div>`;
  }).join("");

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

  // Per-machine approve
  machineApproveList.querySelectorAll(".tw-machine-approve").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const machineId = btn.dataset.machineApprove;
      const targetSel = machineApproveList.querySelector(`select[data-machine-target="${machineId}"]`);
      const target = targetSel?.value || "claude";
      btn.disabled = true;
      btn.textContent = "⏳";

      try {
        const res = await fetch(apiUrl("/api/teamwork/approve-machine"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ machineId, target })
        });
        const data = await res.json();
        if (data.ok) {
          btn.textContent = "✅";
          btn.style.background = "#0984e3";
          // Refresh snapshot for this machine after 3s
          setTimeout(loadSnapshots, 3000);
        } else {
          btn.textContent = data.error === "offline" ? "⏭️ Offline" : "❌";
          btn.style.background = "#c1121f";
        }
        setTimeout(() => {
          btn.textContent = "Aprobar";
          btn.style.background = "";
          btn.disabled = false;
        }, 3000);
      } catch {
        btn.textContent = "❌";
        setTimeout(() => { btn.textContent = "Aprobar"; btn.style.background = ""; btn.disabled = false; }, 2000);
      }
    });
  });

  // Toggle monitor size
  machineApproveList.querySelectorAll(".tw-machine-monitor").forEach((mon) => {
    mon.addEventListener("click", () => {
      mon.classList.toggle("small");
      mon.classList.toggle("expanded");
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
  resultEl.className = "tw-approve-result";

  try {
    const res = await fetch(apiUrl("/api/teamwork/approve"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target })
    });
    const data = await res.json();
    const okList = data.results.filter((r) => r.ok);
    const failList = data.results.filter((r) => !r.ok && !r.skipped);
    const skipped = data.results.filter((r) => r.skipped);

    // Visual feedback with icons
    const parts = [];
    for (const r of okList) parts.push(`✅ ${r.machine}`);
    for (const r of failList) parts.push(`❌ ${r.machine}`);
    if (skipped.length) parts.push(`⏭️ ${skipped.length} offline`);

    resultEl.innerHTML = `<strong>${okList.length} aprobados</strong> — ${parts.join(" | ")}`;
    resultEl.classList.add(okList.length > 0 ? "tw-approve-success" : "tw-approve-error");

    // Refresh snapshots after 4s to show updated screens (save result first)
    const savedResult = resultEl.innerHTML;
    const savedClass = resultEl.className;
    setTimeout(() => {
      loadSnapshots();
      // Restore result after snapshot refresh
      setTimeout(() => {
        resultEl.innerHTML = savedResult;
        resultEl.className = savedClass;
      }, 500);
    }, 4000);
  } catch (err) {
    resultEl.textContent = `Error: ${err.message}`;
    resultEl.classList.add("tw-approve-error");
  }

  btn.disabled = false;
  btn.textContent = target === "claude" ? "Aprobar Claude" : "Aprobar Codex";
}

quickInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    const prompt = quickInput.value.trim();
    if (prompt) {
      handleQuickCommand(prompt).then((handled) => {
        if (!handled) sendToAll(prompt);
      });
    }
    else showFeedback("Escribe un prompt", false);
  }
});

sendAllBtn.addEventListener("click", () => {
  const prompt = quickInput.value.trim();
  if (prompt) {
    handleQuickCommand(prompt).then((handled) => {
      if (!handled) sendToAll(prompt);
    });
  }
  else showFeedback("Escribe un prompt", false);
});

onboardingAllBtn.addEventListener("click", () => sendOnboardingAll());

approveClaudeBtn.addEventListener("click", () => approveAll("claude", approveClaudeBtn, approveClaudeResult));
approveCodexBtn.addEventListener("click", () => approveAll("codex", approveCodexBtn, approveCodexResult));

function updateSnapshotsInPlace(snapshots) {
  for (const m of machines) {
    const row = machineApproveList.querySelector(`.tw-machine-row[data-id="${m.id}"]`);
    if (!row) return renderMachineApproveList(snapshots); // first render
    const mon = row.querySelector(".tw-machine-monitor");
    const snap = snapshots?.[m.id];
    const multiLabels = ["Studio", "Claude", "Codex"];
    if (snap && snap.type === "images") {
      const t = Date.now();
      const imgs = mon.querySelectorAll(".tw-multi-screen img");
      if (imgs.length === snap.images.length) {
        snap.images.forEach((imgPath, i) => {
          const src = (imgPath.startsWith("/") ? apiUrl(imgPath) : imgPath) + `?t=${t}`;
          const preload = new Image();
          preload.onload = () => { imgs[i].src = src; };
          preload.src = src;
        });
        const timeEl = mon.querySelector(".tw-machine-monitor-time");
        if (timeEl) timeEl.textContent = formatTimeShort(snap.updatedAt);
      } else {
        const orients = snap.orientations || snap.images.map(() => "portrait");
        mon.innerHTML = `<div class="tw-multi-monitor">${snap.images.map((imgPath, i) => {
          const src = (imgPath.startsWith("/") ? apiUrl(imgPath) : imgPath) + `?t=${t}`;
          return `<div class="tw-multi-screen ${orients[i]}"><img src="${src}" alt="${multiLabels[i]}"><span class="tw-screen-label">${multiLabels[i]}</span></div>`;
        }).join("")}</div><span class="tw-machine-monitor-time">${formatTimeShort(snap.updatedAt)}</span>`;
      }
    } else if (snap && snap.type === "image") {
      const imgSrc = snap.image.startsWith("/") ? apiUrl(snap.image) : snap.image;
      const cacheBust = imgSrc.includes("?") ? `&t=${Date.now()}` : `?t=${Date.now()}`;
      const newSrc = `${imgSrc}${cacheBust}`;
      const img = mon.querySelector("img");
      if (img) {
        const preload = new Image();
        preload.onload = () => {
          img.src = newSrc;
          const timeEl = mon.querySelector(".tw-machine-monitor-time");
          if (timeEl) timeEl.textContent = formatTimeShort(snap.updatedAt);
        };
        preload.src = newSrc;
      } else {
        mon.innerHTML = `<img src="${newSrc}" alt="${m.name}" style="width:100%;height:100%;object-fit:cover;border-radius:6px;"><span class="tw-machine-monitor-time">${formatTimeShort(snap.updatedAt)}</span>`;
      }
    } else if (snap && snap.text) {
      mon.innerHTML = `<pre>${snap.text.replace(/</g, "&lt;")}</pre><span class="tw-machine-monitor-time">${formatTimeShort(snap.updatedAt)}</span>`;
    }
    // Update app badges
    const statusEl = row.querySelector(".tw-app-status");
    if (statusEl) {
      statusEl.innerHTML =
        (snap?.claudeState ? `<span class="tw-app-tag claude" title="Claude: ${snap.claudeState}">C</span>` : "") +
        (snap?.codexState ? `<span class="tw-app-tag codex" title="Codex: ${snap.codexState}">X</span>` : "");
    }
  }

  // Re-sort rows solo si el orden ha cambiado (evita reflow innecesario)
  const rows = [...machineApproveList.querySelectorAll(".tw-machine-row")];
  const sorted = [...rows].sort((a, b) => (snapshots?.[b.dataset.id] ? 1 : 0) - (snapshots?.[a.dataset.id] ? 1 : 0));
  const orderChanged = rows.some((r, i) => r !== sorted[i]);
  if (orderChanged) sorted.forEach((row) => machineApproveList.appendChild(row));
}

async function loadSnapshots() {
  try {
    const res = await fetch("./snapshots.json", { cache: "no-store" });
    const snapshots = await res.json();
    const hasRows = machineApproveList.querySelector(".tw-machine-row");
    if (hasRows) {
      updateSnapshotsInPlace(snapshots);
    } else {
      renderMachineApproveList(snapshots);
    }
  } catch {
    // silently fail
  }
}

// ─── Watchdog toggle & stats ───────────────────────────────────────

const watchdogToggle = document.querySelector("#watchdogToggle");
const watchdogPulse = document.querySelector("#watchdogPulse");
let watchdogStats = {};

watchdogToggle.addEventListener("change", async () => {
  const enabled = watchdogToggle.checked;
  watchdogPulse.classList.toggle("off", !enabled);
  try {
    await fetch(apiUrl("/api/teamwork/watchdog"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled })
    });
  } catch { /* ignore */ }
});

async function loadWatchdogStats() {
  try {
    const res = await fetch(apiUrl("/api/teamwork/watchdog"), { cache: "no-store" });
    const data = await res.json();
    if (data.ok) {
      watchdogToggle.checked = data.enabled;
      watchdogPulse.classList.toggle("off", !data.enabled);
      watchdogStats = data.perMachine || {};
      updateWatchdogBadges();
    }
  } catch { /* ignore */ }
}

function updateWatchdogBadges() {
  document.querySelectorAll(".tw-auto-badge").forEach((badge) => {
    const machineId = badge.dataset.watchdogMachine;
    const stats = watchdogStats[machineId];
    if (stats) {
      const total = (stats.claudeCount || 0) + (stats.codexCount || 0);
      if (total > 0) {
        badge.textContent = `🤖 ${total}`;
        badge.title = `Claude: ${stats.claudeCount || 0} | Codex: ${stats.codexCount || 0}`;
        badge.classList.add("has-approvals");
      } else {
        badge.textContent = "🤖 0";
        badge.title = "Sin auto-aprobaciones";
        badge.classList.remove("has-approvals");
      }
    }
  });
}

// ─── Init ──────────────────────────────────────────────────────────

loadMachines();
loadHistory();
setTimeout(loadSnapshots, 2000);
setTimeout(loadWatchdogStats, 3000);
setInterval(loadHistory, 10_000);
setInterval(loadSnapshots, 30_000);
setInterval(loadWatchdogStats, 15_000);
