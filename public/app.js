const summaryNode = document.querySelector("#summary");
const machinesNode = document.querySelector("#machines");
const template = document.querySelector("#machine-template");

function formatDate(value) {
  try {
    return new Date(value).toLocaleString("es-ES");
  } catch {
    return value;
  }
}

function createSummary(data) {
  const machines = data.machines;
  const counts = {
    total: machines.length,
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

async function updateStatus(id, status, note) {
  await fetch(`/api/machines/${id}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, note })
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

    fragment.querySelector(".member").textContent = machine.member;
    fragment.querySelector(".name").textContent = machine.name;
    fragment.querySelector(".id").textContent = machine.id;
    fragment.querySelector(".location").textContent = machine.location;
    fragment.querySelector(".platform").textContent = machine.platform;
    fragment.querySelector(".last-seen").textContent = formatDate(machine.lastSeen);
    fragment.querySelector(".note").textContent = machine.note;

    badge.textContent = machine.status;
    badge.classList.add(`status-${machine.status}`);
    statusSelect.value = machine.status;
    noteInput.value = machine.note;

    saveButton.addEventListener("click", async () => {
      saveButton.disabled = true;
      saveButton.textContent = "Guardando...";
      await updateStatus(machine.id, statusSelect.value, noteInput.value);
      await load();
    });

    card.dataset.machineId = machine.id;
    machinesNode.append(fragment);
  }
}

async function load() {
  const response = await fetch("/api/machines");
  const data = await response.json();
  createSummary(data);
  renderMachines(data);
}

load();
