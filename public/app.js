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

function formatDate(value) {
  try {
    return new Date(value).toLocaleString("es-ES");
  } catch {
    return value;
  }
}

function createSummary(data) {
  const machines = data.machines;
  const members = new Set(machines.map((item) => item.member));
  const council = machines.filter((item) => (item.unitType || "council") === "council");
  const pcs = machines.filter((item) => item.unitType === "worker");
  const counts = [
    ["maquinas", machines.length],
    ["miembros", members.size],
    ["consejo", council.length],
    ["pcs", pcs.length],
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

  for (const machine of machines) {
    const fragment = template.content.cloneNode(true);
    const card = fragment.querySelector(".card");
    const badge = fragment.querySelector(".status-badge");
    const saveButton = fragment.querySelector(".save-button");
    const statusSelect = fragment.querySelector(".status-select");
    const noteInput = fragment.querySelector(".note-input");
    const focusInput = fragment.querySelector(".focus-input");
    const noteNode = fragment.querySelector(".note");
    const focusBox = fragment.querySelector(".focus-box");

    fragment.querySelector(".member").textContent = machine.member;
    fragment.querySelector(".name").textContent = machine.name;
    fragment.querySelector(".role").textContent = machine.role ?? "Sin rol definido";
    fragment.querySelector(".id").textContent = machine.id;
    fragment.querySelector(".machine-role").textContent = machine.machineRole ?? "Sin clasificar";
    fragment.querySelector(".location").textContent = machine.location;
    fragment.querySelector(".platform").textContent = machine.platform;
    fragment.querySelector(".color").textContent = machine.color ?? "—";
    fragment.querySelector(".last-seen").textContent = formatDate(machine.lastSeen);
    noteNode.textContent = machine.note;
    fragment.querySelector(".current-focus").textContent = machine.currentFocus ?? "Sin foco operativo";

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
      noteNode.textContent = `${machine.note} Canal remoto: pendiente.`;
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
  try {
    const response = await fetch("/api/machines", { cache: "no-store" });
    if (!response.ok) {
      throw new Error("api unavailable");
    }

    isStaticMode = false;
    return await response.json();
  } catch {
    const response = await fetch("./machines.json?v=20260331-4", { cache: "no-store" });
    isStaticMode = true;
    return await response.json();
  }
}

async function load() {
  const data = await fetchData();
  createSummary(data);
  renderMachines(data);
}

load();
