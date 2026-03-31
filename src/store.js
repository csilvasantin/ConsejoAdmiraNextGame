import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const DATA_PATH = resolve(import.meta.dirname, "../data/machines.json");
const DEFAULT_LOCATION = "Madrid";
const DEFAULT_PLATFORM = "macOS";
const DEFAULT_COLOR = "plata";
const DEFAULT_STATUS = "maintenance";

function cleanString(value, fallback = "") {
  if (typeof value !== "string") {
    return fallback;
  }

  const trimmed = value.trim();
  return trimmed || fallback;
}

function normalizeToken(value) {
  return cleanString(value)
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function buildHost(alias, tailnet) {
  const normalizedAlias = normalizeToken(alias);
  if (!normalizedAlias) {
    return "";
  }

  return tailnet ? `${normalizedAlias}.${tailnet}` : normalizedAlias;
}

function buildMachineId(payload, machines) {
  const baseToken =
    normalizeToken(payload.hostAlias) ||
    normalizeToken(payload.machineName) ||
    normalizeToken(payload.member) ||
    "equipo";
  const baseId = baseToken.startsWith("admira-") ? baseToken : `admira-${baseToken}`;
  let candidate = baseId;
  let suffix = 2;

  while (machines.some((machine) => machine.id === candidate)) {
    candidate = `${baseId}-${suffix}`;
    suffix += 1;
  }

  return candidate;
}

function buildConnectCommand({ enabled, sshUser, tailscaleIp, hostAlias }) {
  if (!enabled || !sshUser) {
    return "";
  }

  if (tailscaleIp) {
    return `ssh ${sshUser}@${tailscaleIp}`;
  }

  if (hostAlias) {
    return `ssh -o ProxyCommand='tailscale nc %h %p' ${sshUser}@${hostAlias}`;
  }

  return "";
}

function buildChecklistSummary(checklist) {
  const labels = [];

  labels.push(checklist.tailscaleReady ? "Tailscale listo" : "Tailscale pendiente");
  labels.push(checklist.sshReady ? "SSH listo" : "SSH pendiente");
  labels.push(checklist.githubReady ? "GitHub listo" : "GitHub pendiente");
  labels.push(checklist.claudeReady ? "Claude listo" : "Claude pendiente");
  labels.push(checklist.codexReady ? "Codex listo" : "Codex pendiente");
  labels.push(checklist.claudeBotReady ? "ClaudeBot listo" : "ClaudeBot pendiente");
  labels.push(checklist.codexBotReady ? "CodexBot listo" : "CodexBot pendiente");

  return labels.join(" | ");
}

function buildNote(payload, checklistSummary) {
  const note = cleanString(payload.note);
  if (note) {
    return note;
  }

  const help = cleanString(payload.onboarding?.needsHelp);
  if (help) {
    return `Alta autoservicio. ${checklistSummary}. Ayuda solicitada: ${help}`;
  }

  return `Alta autoservicio. ${checklistSummary}.`;
}

export async function readMachines() {
  const raw = await readFile(DATA_PATH, "utf8");
  return JSON.parse(raw);
}

export async function writeMachines(data) {
  await writeFile(DATA_PATH, JSON.stringify(data, null, 2) + "\n", "utf8");
}

export async function updateMachineStatus(id, status, note = "") {
  const data = await readMachines();
  const machine = data.machines.find((item) => item.id === id);
  if (!machine) {
    return null;
  }

  machine.status = status;
  if (typeof note === "string" && note.trim()) {
    machine.note = note.trim();
  }
  machine.lastSeen = new Date().toISOString();
  data.updatedAt = new Date().toISOString();

  await writeMachines(data);
  return machine;
}

export async function updateMachineSync(id, payload) {
  const data = await readMachines();
  const machine = data.machines.find((item) => item.id === id);
  if (!machine) {
    return null;
  }

  if (payload.status) {
    machine.status = payload.status;
  }

  if (typeof payload.note === "string") {
    machine.note = payload.note.trim() || machine.note;
  }

  if (typeof payload.currentFocus === "string") {
    machine.currentFocus = payload.currentFocus.trim() || machine.currentFocus;
  }

  machine.lastSeen = new Date().toISOString();
  data.updatedAt = new Date().toISOString();

  await writeMachines(data);
  return machine;
}

export async function createMachineEntry(payload) {
  const data = await readMachines();
  const member = cleanString(payload.member);
  const role = cleanString(payload.role);
  const machineName = cleanString(payload.machineName);
  const machineRole = cleanString(payload.machineRole, "Equipo principal");
  const location = cleanString(payload.location, DEFAULT_LOCATION);
  const platform = cleanString(payload.platform, DEFAULT_PLATFORM);
  const color = cleanString(payload.color, DEFAULT_COLOR);
  const status = cleanString(payload.status, DEFAULT_STATUS);
  const sshUser = cleanString(payload.sshUser, "csilvasantin");
  const hostAlias = normalizeToken(payload.hostAlias);
  const tailscaleIp = cleanString(payload.tailscaleIp);
  const tailnet = cleanString(data.tailnet);
  const tailscaleHost = buildHost(hostAlias, tailnet);
  const remoteReady = Boolean(payload.remoteReady);
  const checklist = {
    tailscaleReady: Boolean(payload.onboarding?.tailscaleReady),
    sshReady: Boolean(payload.onboarding?.sshReady),
    githubReady: Boolean(payload.onboarding?.githubReady),
    claudeReady: Boolean(payload.onboarding?.claudeReady),
    codexReady: Boolean(payload.onboarding?.codexReady),
    claudeBotReady: Boolean(payload.onboarding?.claudeBotReady),
    codexBotReady: Boolean(payload.onboarding?.codexBotReady),
    needsHelp: cleanString(payload.onboarding?.needsHelp)
  };

  if (!member || !role || !machineName) {
    throw new Error("member, role y machineName son obligatorios");
  }

  if (tailscaleHost && data.machines.some((machine) => machine.ssh?.host === tailscaleHost)) {
    throw new Error("Ya existe una maquina con ese host Tailscale");
  }

  const machineId = buildMachineId({ ...payload, hostAlias }, data.machines);
  const checklistSummary = buildChecklistSummary(checklist);
  const now = new Date().toISOString();
  const machine = {
    id: machineId,
    unitType: cleanString(payload.unitType, "council"),
    color,
    member,
    role,
    name: machineName,
    machineRole,
    location,
    platform,
    status,
    lastSeen: now,
    currentFocus: cleanString(payload.currentFocus, "Onboarding y puesta a punto del equipo"),
    note: buildNote(payload, checklistSummary),
    ssh: {
      enabled: remoteReady,
      user: sshUser,
      host: tailscaleHost,
      ip_tailscale: tailscaleIp,
      connect_tailscale: buildConnectCommand({ enabled: remoteReady, sshUser, tailscaleIp, hostAlias }),
      hostAlias
    },
    intake: {
      source: "new-member-form",
      submittedAt: now,
      teamArea: cleanString(payload.teamArea),
      checklist
    }
  };

  data.machines.push(machine);
  data.updatedAt = now;

  await writeMachines(data);
  return machine;
}
