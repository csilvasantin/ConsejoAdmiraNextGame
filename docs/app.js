const summaryNode = document.querySelector("#summary");
const machinesNode = document.querySelector("#machines");
const template = document.querySelector("#machine-template");
let isStaticMode = false;
const STATUS_ORDER = ["online", "idle", "busy", "offline", "maintenance"];
const GROUP_META = {
  council: {
    title: "Consejo de Administracion",
    subtitle: "Todos los Mac del consejo: decision, coordinacion y supervision."
  },
  worker: {
    title: "Equipo",
    subtitle: "Todos los PC del equipo: ejecucion, validacion y operativa rutinaria."
  }
};
const CHECKLIST_FIELDS = [
  { key: "tailscaleReady", label: "Tailscale" },
  { key: "sshReady", label: "SSH" },
  { key: "githubReady", label: "GitHub" },
  { key: "claudeReady", label: "Claude" },
  { key: "codexReady", label: "Codex" },
  { key: "claudeBotReady", label: "ClaudeBot" },
  { key: "codexBotReady", label: "CodexBot" }
];
const STATUS_PRIORITY = {
  maintenance: 0,
  offline: 1,
  busy: 2,
  idle: 3,
  online: 4
};

function getChecklist(machine) {
  return machine.intake?.checklist ?? null;
}

function getChecklistEntries(machine) {
  const checklist = getChecklist(machine);
  if (!checklist) {
    return [];
  }

  return CHECKLIST_FIELDS.map((field) => ({
    label: field.label,
    ready: Boolean(checklist[field.key])
  }));
}

function getChecklistProgress(machine) {
  const entries = getChecklistEntries(machine);
  const completed = entries.filter((entry) => entry.ready).length;
  return {
    entries,
    completed,
    total: entries.length
  };
}

function hasActiveHelp(machine) {
  const help = machine.intake?.checklist?.needsHelp?.trim();
  if (!help) {
    return false;
  }

  const normalized = help
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();

  return ![
    "ninguna",
    "ninguno",
    "nada",
    "no",
    "sin ayuda",
    "sin bloqueos",
    "sin bloqueo"
  ].some((prefix) => normalized.startsWith(prefix));
}

function getTeamArea(machine) {
  return machine.intake?.teamArea?.trim() || "Sin clasificar";
}

function getRemoteStatus(machine) {
  return machine.ssh?.enabled ? "SSH listo" : "Sin SSH";
}

function getRemoteCommand(machine) {
  return machine.ssh?.connect_tailscale || machine.ssh?.host || "";
}

function getMachineSignals(machine) {
  const progress = getChecklistProgress(machine);
  const signals = [];

  if (hasActiveHelp(machine)) {
    signals.push({ label: "Atencion requerida", tone: "danger" });
  } else if (progress.total && progress.completed < progress.total) {
    signals.push({ label: `Onboarding ${progress.completed}/${progress.total}`, tone: "warm" });
  } else if (progress.total && progress.completed === progress.total) {
    signals.push({ label: "Onboarding completo", tone: "ok" });
  } else {
    signals.push({ label: "Inventario activo", tone: "muted" });
  }

  signals.push({
    label: machine.ssh?.enabled ? "SSH remoto disponible" : "Sin acceso remoto",
    tone: machine.ssh?.enabled ? "ok" : "muted"
  });

  if (machine.intake?.teamArea) {
    signals.push({ label: getTeamArea(machine), tone: "neutral" });
  }

  return signals;
}

function compareMachines(left, right) {
  const leftHelp = hasActiveHelp(left);
  const rightHelp = hasActiveHelp(right);
  if (leftHelp !== rightHelp) {
    return leftHelp ? -1 : 1;
  }

  const leftProgress = getChecklistProgress(left);
  const rightProgress = getChecklistProgress(right);
  const leftIncomplete = leftProgress.total > 0 && leftProgress.completed < leftProgress.total;
  const rightIncomplete = rightProgress.total > 0 && rightProgress.completed < rightProgress.total;
  if (leftIncomplete !== rightIncomplete) {
    return leftIncomplete ? -1 : 1;
  }

  const leftRemote = Boolean(left.ssh?.enabled);
  const rightRemote = Boolean(right.ssh?.enabled);
  if (leftRemote !== rightRemote) {
    return leftRemote ? 1 : -1;
  }

  const statusDiff = (STATUS_PRIORITY[left.status] ?? 99) - (STATUS_PRIORITY[right.status] ?? 99);
  if (statusDiff !== 0) {
    return statusDiff;
  }

  const leftSeen = Date.parse(left.lastSeen) || 0;
  const rightSeen = Date.parse(right.lastSeen) || 0;
  return rightSeen - leftSeen;
}

function formatDate(value) {
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value || "Sin registro";
    }
    return date.toLocaleString("es-ES");
  } catch {
    return value || "Sin registro";
  }
}

function createSummary(data) {
  const machines = data.machines;
  const members = new Set(machines.map((item) => item.member));
  const council = machines.filter((item) => (item.unitType || "council") === "council");
  const pcs = machines.filter((item) => item.unitType === "worker");
  const remoteReady = machines.filter((item) => item.ssh?.enabled).length;
  const completeOnboarding = machines.filter((item) => {
    const progress = getChecklistProgress(item);
    return progress.total > 0 && progress.completed === progress.total;
  }).length;
  const helpCount = machines.filter((item) => hasActiveHelp(item)).length;
  const counts = [
    ["maquinas", machines.length],
    ["miembros", members.size],
    ["consejo", council.length],
    ["pcs", pcs.length],
    ["ssh listo", remoteReady],
    ["alta completa", completeOnboarding],
    ["con ayuda", helpCount],
    ...STATUS_ORDER.map((status) => [
      status,
      machines.filter((item) => item.status === status).length
    ])
  ];

  summaryNode.innerHTML = "";
  for (const [label, value] of counts) {
    const card = document.createElement("div");
    card.className = "summary-card";
    card.innerHTML = `<strong>${value}</strong><span>${label}</span>`;
    summaryNode.append(card);
  }
}

async function syncMachine(id, status, note, currentFocus) {
  if (isStaticMode) {
    return;
  }

  await fetch(`/api/machines/${id}/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, note, currentFocus })
  });
}

function renderSection(groupKey, machines) {
  const section = document.createElement("section");
  section.className = `fleet-section fleet-section-${groupKey}`;
  section.innerHTML = `
    <div class="fleet-head">
      <div>
        <p class="fleet-kicker">${groupKey === "council" ? "Consejo" : "Equipo"}</p>
        <h2>${GROUP_META[groupKey].title}</h2>
        <p>${GROUP_META[groupKey].subtitle}</p>
      </div>
    </div>
    <div class="grid"></div>
  `;

  const grid = section.querySelector(".grid");

  for (const machine of [...machines].sort(compareMachines)) {
    const fragment = template.content.cloneNode(true);
    const card = fragment.querySelector(".card");
    const badge = fragment.querySelector(".status-badge");
    const saveButton = fragment.querySelector(".save-button");
    const statusSelect = fragment.querySelector(".status-select");
    const noteInput = fragment.querySelector(".note-input");
    const focusInput = fragment.querySelector(".focus-input");
    const noteNode = fragment.querySelector(".note");
    const focusBox = fragment.querySelector(".focus-box");
    const signalStrip = fragment.querySelector(".signal-strip");
    const remoteCopy = fragment.querySelector(".remote-copy");
    const checklistBox = fragment.querySelector(".checklist-box");
    const checklistBadges = fragment.querySelector(".checklist-badges");
    const checklistSummary = fragment.querySelector(".section-summary");
    const helpBox = fragment.querySelector(".help-box");
    const helpText = fragment.querySelector(".help-text");
    const checklist = getChecklistProgress(machine);
    const help = machine.intake?.checklist?.needsHelp?.trim() || "";

    fragment.querySelector(".member").textContent = machine.member;
    fragment.querySelector(".name").textContent = machine.name;
    fragment.querySelector(".role").textContent = machine.role ?? "Sin rol definido";
    fragment.querySelector(".id").textContent = machine.id;
    fragment.querySelector(".machine-role").textContent = machine.machineRole ?? "Sin clasificar";
    fragment.querySelector(".team-area").textContent = getTeamArea(machine);
    fragment.querySelector(".location").textContent = machine.location;
    fragment.querySelector(".platform").textContent = machine.platform;
    fragment.querySelector(".remote-status").textContent = getRemoteStatus(machine);
    fragment.querySelector(".color").textContent = machine.color ?? "—";
    fragment.querySelector(".last-seen").textContent = formatDate(machine.lastSeen);
    noteNode.textContent = machine.note || "Sin nota operativa";
    fragment.querySelector(".current-focus").textContent = machine.currentFocus ?? "Sin foco operativo";
    remoteCopy.textContent = getRemoteCommand(machine) || "Sin comando remoto registrado todavía";

    for (const signal of getMachineSignals(machine)) {
      const pill = document.createElement("span");
      pill.className = `signal-pill signal-${signal.tone}`;
      pill.textContent = signal.label;
      signalStrip.append(pill);
    }

    checklistBadges.innerHTML = "";
    if (checklist.total) {
      checklistSummary.textContent = `${checklist.completed}/${checklist.total} bloques listos`;
      for (const entry of checklist.entries) {
        const chip = document.createElement("span");
        chip.className = `checklist-pill ${entry.ready ? "is-ready" : "is-pending"}`;
        chip.textContent = entry.label;
        checklistBadges.append(chip);
      }
    } else {
      checklistSummary.textContent = "Sin ficha de alta";
      const chip = document.createElement("span");
      chip.className = "checklist-pill is-muted";
      chip.textContent = "Inventario previo al formulario";
      checklistBadges.append(chip);
    }

    helpBox.hidden = !hasActiveHelp(machine);
    if (!helpBox.hidden) {
      helpText.textContent = help;
    }
    checklistBox.classList.toggle("is-complete", checklist.total > 0 && checklist.completed === checklist.total);

    badge.textContent = machine.status;
    badge.classList.add(`status-${machine.status}`);
    statusSelect.value = machine.status;
    noteInput.value = machine.note;
    focusInput.value = machine.currentFocus ?? "";
    card.classList.add(groupKey === "worker" ? "card-worker" : "card-council");

    if (machine.unitType === "worker") {
      const chips = document.createElement("div");
      chips.className = "tag-row";
      const profile = machine.agentProfile ? `<span class="fleet-tag fleet-tag-profile">${machine.agentProfile}</span>` : "";
      const capabilities = (machine.capabilities || []).map((item) => `<span class="fleet-tag">${item}</span>`).join("");
      chips.innerHTML = `<span class="fleet-tag fleet-tag-type">pc</span>${profile}${capabilities}`;
      focusBox.before(chips);
    }

    if (isStaticMode) {
      statusSelect.disabled = true;
      noteInput.disabled = true;
      focusInput.disabled = true;
      saveButton.disabled = true;
      saveButton.textContent = "Solo lectura";
    }

    if (machine.ssh?.enabled === false) {
      noteNode.textContent = `${noteNode.textContent} Canal remoto: pendiente.`;
    }

    saveButton.addEventListener("click", async () => {
      if (isStaticMode) {
        return;
      }

      saveButton.disabled = true;
      saveButton.textContent = "Sincronizando...";
      await syncMachine(machine.id, statusSelect.value, noteInput.value, focusInput.value);
      await load();
    });

    card.dataset.machineId = machine.id;
    grid.append(fragment);
  }

  return section;
}

function renderMachines(data) {
  machinesNode.innerHTML = "";
  const grouped = {
    council: data.machines.filter((machine) => (machine.unitType || "council") === "council"),
    worker: data.machines.filter((machine) => machine.unitType === "worker")
  };

  for (const [groupKey, machines] of Object.entries(grouped)) {
    if (machines.length) {
      machinesNode.append(renderSection(groupKey, machines));
    }
  }
}

async function fetchData() {
  const response = await fetch("./machines.json?v=20260401-2", { cache: "no-store" });
  isStaticMode = true;
  return await response.json();
}

async function load() {
  const data = await fetchData();
  createSummary(data);
  renderMachines(data);
}

load();
