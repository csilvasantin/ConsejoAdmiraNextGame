import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const DATA_PATH = resolve(import.meta.dirname, "../data/machines.json");

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
