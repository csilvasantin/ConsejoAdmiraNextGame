import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, resolve } from "node:path";

import { createMachineEntry, readMachines, updateMachineStatus, updateMachineSync } from "./store.js";
import { sendPromptToMachine, resolveMachineName, getCapture, getImageBuffer, approveAll, approveMachine, getAllSnapshots, getReachableMachines, getWatchdogState, setWatchdogEnabled, setMachineWatchdog, sendOnboardingToAll, startWatchdog } from "./ssh-exec.js";
import { addEntry, getHistory } from "./teamwork-store.js";

const PORT = 3030;
const HOST = "0.0.0.0";
const PUBLIC_DIR = resolve(import.meta.dirname, "../public");

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8"
};
const VALID_STATUSES = new Set(["online", "idle", "busy", "offline", "maintenance"]);

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type"
};
const FRIENDLY_ROUTES = new Map([
  ["/alta", "/new-member.html?preset=creative-macbook-air-clean"],
  ["/creativa", "/new-member.html?preset=creative-macbook-air-clean"],
  ["/alta-creativa", "/new-member.html?preset=creative-macbook-air-clean"]
]);
const DEFAULT_ONBOARDING_PROMPT =
  "Haz onboarding leyendo el repositorio onboarding de Admira Next primero. Carga el contexto compartido, identifica los repositorios activos y queda listo para continuar sin pedir de nuevo el contexto base.";

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, { "Content-Type": "application/json; charset=utf-8", ...CORS_HEADERS });
  response.end(JSON.stringify(payload));
}

async function serveStatic(pathname, response) {
  const filePath = pathname === "/" ? resolve(PUBLIC_DIR, "index.html") : resolve(PUBLIC_DIR, `.${pathname}`);
  const ext = extname(filePath);
  const contentType = MIME_TYPES[ext] || "text/plain; charset=utf-8";
  try {
    const file = await readFile(filePath);
    response.writeHead(200, { "Content-Type": contentType });
    response.end(file);
  } catch {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Not found");
  }
}

function readRequestBody(request) {
  return new Promise((resolveBody, rejectBody) => {
    let body = "";
    request.on("data", (chunk) => {
      body += chunk;
    });
    request.on("end", () => resolveBody(body));
    request.on("error", rejectBody);
  });
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url || "/", `http://${request.headers.host}`);

  if (request.method === "OPTIONS") {
    response.writeHead(204, CORS_HEADERS);
    response.end();
    return;
  }

  if ((request.method === "GET" || request.method === "HEAD") && FRIENDLY_ROUTES.has(url.pathname)) {
    response.writeHead(302, { Location: FRIENDLY_ROUTES.get(url.pathname) });
    response.end();
    return;
  }

  if (url.pathname === "/api/machines") {
    if (request.method !== "GET" && request.method !== "POST") {
      sendJson(response, 405, { error: "Method not allowed" });
      return;
    }

    if (request.method === "GET") {
      const data = await readMachines();
      sendJson(response, 200, data);
      return;
    }

    if (request.method === "POST") {
      try {
        const rawBody = await readRequestBody(request);
        const parsed = rawBody ? JSON.parse(rawBody) : {};
        if (!VALID_STATUSES.has(parsed.status || "maintenance")) {
          sendJson(response, 400, { error: "Invalid status" });
          return;
        }

        const machine = await createMachineEntry(parsed);
        sendJson(response, 201, { ok: true, machine });
      } catch (error) {
        sendJson(response, 400, { error: error instanceof Error ? error.message : "No se pudo crear la maquina" });
      }
      return;
    }
  }

  if (request.method === "POST" && url.pathname.startsWith("/api/machines/") && url.pathname.endsWith("/status")) {
    const parts = url.pathname.split("/");
    const id = parts[3];
    const rawBody = await readRequestBody(request);
    const parsed = rawBody ? JSON.parse(rawBody) : {};
    const status = parsed.status;
    const note = parsed.note ?? "";

    if (!VALID_STATUSES.has(status)) {
      sendJson(response, 400, { error: "Invalid status" });
      return;
    }

    const updated = await updateMachineStatus(id, status, note);
    if (!updated) {
      sendJson(response, 404, { error: "Machine not found" });
      return;
    }

    sendJson(response, 200, { ok: true, machine: updated });
    return;
  }

  if (request.method === "POST" && url.pathname.startsWith("/api/machines/") && url.pathname.endsWith("/sync")) {
    const parts = url.pathname.split("/");
    const id = parts[3];
    const rawBody = await readRequestBody(request);
    const parsed = rawBody ? JSON.parse(rawBody) : {};
    const status = parsed.status;

    if (!VALID_STATUSES.has(status)) {
      sendJson(response, 400, { error: "Invalid status" });
      return;
    }

    const updated = await updateMachineSync(id, {
      status,
      note: parsed.note ?? "",
      currentFocus: parsed.currentFocus ?? ""
    });

    if (!updated) {
      sendJson(response, 404, { error: "Machine not found" });
      return;
    }

    sendJson(response, 200, { ok: true, machine: updated });
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/teamwork/send") {
    const rawBody = await readRequestBody(request);
    const parsed = rawBody ? JSON.parse(rawBody) : {};
    let { machineId, prompt, target } = parsed;
    target = target || "terminal";

    if (!machineId || !prompt) {
      sendJson(response, 400, { error: "machineId y prompt son obligatorios" });
      return;
    }

    prompt = prompt.trim();
    if (!prompt) {
      sendJson(response, 400, { error: "El prompt no puede estar vacío" });
      return;
    }

    const data = await readMachines();
    const machine = data.machines.find((m) => m.id === machineId);
    if (!machine) {
      const resolved = resolveMachineName(data.machines, machineId);
      if (resolved) {
        machineId = resolved.id;
      }
    }

    const result = await sendPromptToMachine(machineId, prompt, target);
    const entry = addEntry(machineId, result.name || machineId, prompt, result.ok, result.error, result.captureId, target);
    sendJson(response, result.ok ? 200 : 502, { ...result, entry });
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/teamwork/send-all") {
    const rawBody = await readRequestBody(request);
    const parsed = rawBody ? JSON.parse(rawBody) : {};
    const prompt = parsed.prompt?.trim();
    const target = parsed.target || "all";
    if (!prompt) {
      sendJson(response, 400, { error: "prompt obligatorio" });
      return;
    }

    const reachable = await getReachableMachines();
    const targets = target === "all" ? ["claude", "codex"] : [target];
    const results = await Promise.allSettled(
      reachable.flatMap((machine) =>
        targets.map((t) => sendPromptToMachine(machine.id, prompt, t))
      )
    );

    const output = results.map((r) => {
      const v = r.value || { ok: false, error: "rejected" };
      return { machine: v.name || v.machine, ok: v.ok, error: v.error };
    });
    sendJson(response, 200, { ok: true, results: output });
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/teamwork/onboarding-all") {
    const rawBody = await readRequestBody(request);
    const parsed = rawBody ? JSON.parse(rawBody) : {};
    const prompt = parsed.prompt?.trim() || DEFAULT_ONBOARDING_PROMPT;
    const results = await sendOnboardingToAll(prompt);
    sendJson(response, 200, { ok: true, prompt, results });
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/teamwork/approve") {
    const rawBody = await readRequestBody(request);
    const parsed = rawBody ? JSON.parse(rawBody) : {};
    const target = parsed.target || "claude";
    const results = await approveAll(target);
    sendJson(response, 200, { ok: true, results });
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/teamwork/approve-machine") {
    const rawBody = await readRequestBody(request);
    const parsed = rawBody ? JSON.parse(rawBody) : {};
    const { machineId, target } = parsed;
    if (!machineId) {
      sendJson(response, 400, { error: "machineId obligatorio" });
      return;
    }
    const result = await approveMachine(machineId, target || "claude");
    sendJson(response, 200, result);
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/teamwork/snapshots") {
    sendJson(response, 200, { ok: true, snapshots: getAllSnapshots() });
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/teamwork/history") {
    sendJson(response, 200, { entries: getHistory() });
    return;
  }

  if (request.method === "GET" && url.pathname.startsWith("/api/teamwork/capture/")) {
    const captureId = url.pathname.split("/").pop();
    const capture = getCapture(captureId);
    if (capture) {
      sendJson(response, 200, { ok: true, ...capture });
    } else {
      sendJson(response, 202, { ok: false, pending: true });
    }
    return;
  }

  if (request.method === "GET" && url.pathname.startsWith("/api/screenshots/")) {
    const id = url.pathname.split("/").pop();
    const buf = getImageBuffer(id);
    if (buf) {
      response.writeHead(200, { "Content-Type": "image/jpeg", "Cache-Control": "no-cache, no-store, must-revalidate", ...CORS_HEADERS });
      response.end(buf);
    } else {
      response.writeHead(404, { "Content-Type": "text/plain" });
      response.end("Not found");
    }
    return;
  }

  // Watchdog endpoints
  if (request.method === "GET" && url.pathname === "/api/teamwork/watchdog") {
    sendJson(response, 200, { ok: true, ...getWatchdogState() });
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/teamwork/watchdog") {
    const rawBody = await readRequestBody(request);
    const parsed = rawBody ? JSON.parse(rawBody) : {};
    setWatchdogEnabled(!!parsed.enabled);
    sendJson(response, 200, { ok: true, enabled: !!parsed.enabled });
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/teamwork/watchdog/machine") {
    const rawBody = await readRequestBody(request);
    const parsed = rawBody ? JSON.parse(rawBody) : {};
    if (!parsed.machineId) {
      sendJson(response, 400, { error: "machineId obligatorio" });
      return;
    }
    setMachineWatchdog(parsed.machineId, !!parsed.enabled);
    sendJson(response, 200, { ok: true, machineId: parsed.machineId, enabled: !!parsed.enabled });
    return;
  }

  await serveStatic(url.pathname, response);
});

server.listen(PORT, HOST, () => {
  console.log(`AdmiraNext Team escuchando en http://${HOST}:${PORT}`);
  startWatchdog(); // Auto-Approve ON por defecto al arrancar
});
