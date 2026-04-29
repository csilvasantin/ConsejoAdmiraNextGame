#!/usr/bin/env node

import os from "node:os";
import path from "node:path";
import fs from "node:fs/promises";
import { chromium } from "playwright-core";

const API_BASE_URL = process.env.COUNCIL_API_BASE_URL || "https://three2-consejoadmiranextgame.onrender.com";
const API_TOKEN = process.env.COUNCIL_API_TOKEN || "admira2026";
const YARIG_URL = process.env.YARIG_URL || "https://www.yarig.ai/tasks";
const ONCE = process.argv.includes("--once");
const DUMP_JSON = process.argv.includes("--dump-json");
const PREPARE_LOGIN = process.argv.includes("--prepare-login");
const POLL_MS = Number(process.env.YARIG_SYNC_POLL_MS || 120000);
const LOGIN_WAIT_MS = Number(process.env.YARIG_LOGIN_WAIT_MS || 300000);

const CHROME_EXECUTABLE =
  process.env.YARIG_CHROME_EXECUTABLE ||
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const CHROME_USER_DATA_DIR =
  process.env.YARIG_CHROME_USER_DATA_DIR ||
  path.join(os.homedir(), "Library/Application Support/Google/Chrome");
const CHROME_PROFILE_DIR = process.env.YARIG_CHROME_PROFILE_DIR || "Profile 1";
const CHROME_AUTOMATION_USER_DATA_DIR =
  process.env.YARIG_AUTOMATION_USER_DATA_DIR ||
  path.join(os.homedir(), "Library/Application Support/Google/Chrome-YarigSync-Profile1");

let context = null;
let page = null;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function log(message, extra = null) {
  if (DUMP_JSON) return;
  const stamp = new Date().toISOString();
  if (extra == null) console.log(`[${stamp}] ${message}`);
  else console.log(`[${stamp}] ${message}`, extra);
}

async function api(pathname, init = {}) {
  const res = await fetch(`${API_BASE_URL}${pathname}`, {
    ...init,
    headers: {
      "X-Council-Token": API_TOKEN,
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    throw new Error(`API ${pathname} -> HTTP ${res.status}: ${(await res.text()).slice(0, 300)}`);
  }
  return res.json();
}

async function launchChromeContext(userDataDir) {
  return chromium.launchPersistentContext(userDataDir, {
    executablePath: CHROME_EXECUTABLE,
    headless: false,
    viewport: null,
    args: [
      `--profile-directory=${CHROME_PROFILE_DIR}`,
      "--disable-blink-features=AutomationControlled",
    ],
  });
}

async function cloneChromeProfile(targetRoot) {
  const sourceProfileDir = path.join(CHROME_USER_DATA_DIR, CHROME_PROFILE_DIR);
  const targetProfileDir = path.join(targetRoot, CHROME_PROFILE_DIR);
  const localStatePath = path.join(CHROME_USER_DATA_DIR, "Local State");
  await fs.mkdir(targetRoot, { recursive: true });
  try {
    await fs.copyFile(localStatePath, path.join(targetRoot, "Local State"));
  } catch {}
  await fs.cp(sourceProfileDir, targetProfileDir, { recursive: true, force: true });
  return targetRoot;
}

async function ensureBrowser() {
  if (page) return page;
  try {
    context = await launchChromeContext(CHROME_USER_DATA_DIR);
  } catch (error) {
    if (!String(error.message || "").includes("ProcessSingleton")) throw error;
    try {
      await fs.access(path.join(CHROME_AUTOMATION_USER_DATA_DIR, CHROME_PROFILE_DIR));
      log("Perfil principal en uso; reutilizando perfil persistente de automatización para Yarig");
    } catch {
      log("Perfil principal en uso; creando perfil persistente de automatización para Yarig");
      await cloneChromeProfile(CHROME_AUTOMATION_USER_DATA_DIR);
    }
    context = await launchChromeContext(CHROME_AUTOMATION_USER_DATA_DIR);
  }
  page = context.pages()[0] || await context.newPage();
  page.setDefaultTimeout(15000);
  return page;
}

async function closeBrowser() {
  if (page) {
    try { await page.close(); } catch {}
    page = null;
  }
  if (context) {
    try { await context.close(); } catch {}
    context = null;
  }
}

function extractTaskBucketsFromText(text) {
  const clean = String(text || "").replace(/\r/g, "").trim();
  const chunks = clean.split(/Tarea añadida el \d{2}\/\d{2}\/\d{4}:/).slice(1);
  const activeTasks = [];
  const doneTasks = [];
  const tasks = [];
  for (const chunk of chunks) {
    const desc = chunk.match(/Descripción:\s*([^\n]+)/)?.[1]?.trim();
    const status = chunk.match(/\b(En proceso|Pendiente|Finalizada|Finalizado)\b/)?.[1]?.trim() || "Pendiente";
    if (!desc) continue;
    const line = `${status} - ${desc}`.slice(0, 240);
    if (/^Finalizad[ao]$/i.test(status)) doneTasks.push(line);
    else activeTasks.push(line);
  }
  return {
    tasks: activeTasks.slice(0, 12),
    done: doneTasks.slice(0, 12),
  };
}

async function fetchVisibleTasks(activePage) {
  await activePage.goto(YARIG_URL, { waitUntil: "domcontentloaded" });
  await activePage.waitForLoadState("domcontentloaded");
  const title = await activePage.title();
  const url = activePage.url();
  if (/login|auth/i.test(url) || !/(^|\.)yarig\.ai/i.test(new URL(url).hostname)) {
    throw new Error(`Yarig.ai no está accesible en sesión reutilizable: ${url}`);
  }
  const bodyText = await activePage.locator("body").innerText();
  if (!bodyText.includes("Mis tareas")) {
    throw new Error(`La página abierta no parece la lista de tareas de Yarig.ai (${title})`);
  }
  return {
    ...extractTaskBucketsFromText(bodyText),
    currentUrl: url,
    title,
  };
}

async function syncOnce() {
  const activePage = await ensureBrowser();
  const { tasks, done } = await fetchVisibleTasks(activePage);
  const current = await api("/api/council/yar-context");
  const payload = {
    focus: current.focus || "",
    doing: current.doing || "",
    done,
    tasks,
    pending: tasks,
    ask: current.ask || "",
  };
  const saved = await api("/api/council/yar-context", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  log(`Sincronizadas ${tasks.length} tareas activas y ${done.length} finalizadas desde Yarig.ai`);
  return saved;
}

async function prepareLoginWindow() {
  const activePage = await ensureBrowser();
  await activePage.goto(YARIG_URL, { waitUntil: "domcontentloaded" });
  await activePage.waitForLoadState("domcontentloaded");
  log("Ventana de Yarig.ai abierta para autenticacion");
  const startedAt = Date.now();
  let lastError = null;
  while ((Date.now() - startedAt) < LOGIN_WAIT_MS) {
    try {
      const payload = await fetchVisibleTasks(activePage);
      log(`Sesion de Yarig.ai lista con ${payload.tasks.length} tareas activas y ${payload.done.length} finalizadas`);
      return payload;
    } catch (error) {
      lastError = error;
      await sleep(1000);
    }
  }
  throw lastError || new Error("Yarig login no se completo a tiempo");
}

async function main() {
  if (PREPARE_LOGIN) {
    const payload = await prepareLoginWindow();
    process.stdout.write(JSON.stringify({
      ok: true,
      prepared: true,
      currentUrl: payload.currentUrl,
      title: payload.title,
      tasks: payload.tasks,
      done: payload.done,
    }));
    await closeBrowser();
    return;
  }
  if (DUMP_JSON) {
    const activePage = await ensureBrowser();
    const payload = await fetchVisibleTasks(activePage);
    process.stdout.write(JSON.stringify(payload));
    await closeBrowser();
    return;
  }
  log(`Yarig sync listo. API: ${API_BASE_URL}`);
  do {
    try {
      await syncOnce();
    } catch (error) {
      log("Fallo de sync Yarig.ai", error.message);
    }
    if (ONCE) break;
    await sleep(POLL_MS);
  } while (true);
}

main().catch(async (error) => {
  console.error(error);
  process.exitCode = 1;
  await closeBrowser();
});
