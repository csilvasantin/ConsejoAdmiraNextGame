const summaryNode = document.querySelector("#summary");
const machinesNode = document.querySelector("#machines");
const template = document.querySelector("#machine-template");
let isStaticMode = false;

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
  const counts = {
    maquinas: machines.length,
    miembros: members.size,
    online: machines.filter((item) => item.status === "online").length,
    busy: machines.filter((item) => item.status === "busy").length,
    offline: machines.filter((item) => item.status === "offline").length,
    maintenance: machines.filter((item) => item.status === "maintenance").length
  };

  summaryNode.innerHTML = "";
  for (const [label, value] of Object.entries(counts)) {
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

function renderMachines(data) {
  machinesNode.innerHTML = "";

  for (const machine of data.machines) {
    const fragment = template.content.cloneNode(true);
    const card = fragment.querySelector(".card");
    const badge = fragment.querySelector(".status-badge");
    const saveButton = fragment.querySelector(".save-button");
    const statusSelect = fragment.querySelector(".status-select");
    const noteInput = fragment.querySelector(".note-input");
    const focusInput = fragment.querySelector(".focus-input");

    fragment.querySelector(".member").textContent = machine.member;
    fragment.querySelector(".name").textContent = machine.name;
    fragment.querySelector(".role").textContent = machine.role ?? "Sin rol definido";
    fragment.querySelector(".id").textContent = machine.id;
    fragment.querySelector(".machine-role").textContent = machine.machineRole ?? "Sin clasificar";
    fragment.querySelector(".location").textContent = machine.location;
    fragment.querySelector(".platform").textContent = machine.platform;
    fragment.querySelector(".last-seen").textContent = formatDate(machine.lastSeen);
    fragment.querySelector(".note").textContent = machine.note;
    fragment.querySelector(".current-focus").textContent = machine.currentFocus ?? "Sin foco operativo";

    badge.textContent = machine.status;
    badge.classList.add(`status-${machine.status}`);
    statusSelect.value = machine.status;
    noteInput.value = machine.note;
    focusInput.value = machine.currentFocus ?? "";
    if (isStaticMode) {
      statusSelect.disabled = true;
      noteInput.disabled = true;
      focusInput.disabled = true;
      saveButton.disabled = true;
      saveButton.textContent = "Solo lectura";
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
    machinesNode.append(fragment);
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
    const response = await fetch("./machines.json?v=20260321-1", { cache: "no-store" });
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
