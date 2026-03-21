import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, resolve } from "node:path";

import { readMachines, updateMachineStatus, updateMachineSync } from "./store.js";

const PORT = 3030;
const HOST = "127.0.0.1";
const PUBLIC_DIR = resolve(import.meta.dirname, "../public");

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8"
};

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, { "Content-Type": "application/json; charset=utf-8" });
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

  if (request.method === "GET" && url.pathname === "/api/machines") {
    const data = await readMachines();
    sendJson(response, 200, data);
    return;
  }

  if (request.method === "POST" && url.pathname.startsWith("/api/machines/") && url.pathname.endsWith("/status")) {
    const parts = url.pathname.split("/");
    const id = parts[3];
    const rawBody = await readRequestBody(request);
    const parsed = rawBody ? JSON.parse(rawBody) : {};
    const status = parsed.status;
    const note = parsed.note ?? "";

    if (!["online", "busy", "offline", "maintenance"].includes(status)) {
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

    if (!["online", "busy", "offline", "maintenance"].includes(status)) {
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

  await serveStatic(url.pathname, response);
});

server.listen(PORT, HOST, () => {
  console.log(`AdmiraNext Team escuchando en http://${HOST}:${PORT}`);
});
