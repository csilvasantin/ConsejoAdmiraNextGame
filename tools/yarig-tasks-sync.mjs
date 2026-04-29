#!/usr/bin/env node

import os from "node:os";
import path from "node:path";
import fs from "node:fs/promises";
import { chromium } from "playwright-core";

const API_BASE_URL = process.env.COUNCIL_API_BASE_URL || "https://three2-consejoadmiranextgame.onrender.com";
const API_TOKEN = process.env.COUNCIL_API_TOKEN || "admira2026";
const YARIG_URL = process.env.YARIG_URL || "https://yarig.ai/tasks";
const ONCE = process.argv.includes("--once");
const POLL_MS = Number(process.env.YARIG_SYNC_POLL_MS || 120000);

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

function extractTasksFromText(text) {
  const clean = String(text || "").replace(/\r/g, "").trim();
  const chunks = clean.split(/Tarea añadida el \d{2}\/\d{2}\/\d{4}:/).slice(1);
  const tasks = [];
  for (const chunk of chunks) {
    const desc = chunk.match(/Descripción:\s*([^\n]+)/)?.[1]?.trim();
    const status = chunk.match(/\b(En proceso|Pendiente)\b/)?.[1]?.trim() || "Pendiente";
    if (!desc) continue;
    tasks.push(`${status} - ${desc}`.slice(0, 240));
  }
  return tasks.slice(0, 12);
}

async function fetchVisibleTasks(activePage) {
  await activePage.goto(YARIG_URL, { waitUntil: "domcontentloaded" });
  await activePage.waitForLoadState("domcontentloaded");
  const title = await activePage.title();
  const url = activePage.url();
  if (/login|auth/i.test(url) || !/yarig\.ai/.test(url)) {
    throw new Error(`Yarig.ai no está accesible en sesión reutilizable: ${url}`);
  }
  const bodyText = await activePage.locator("body").innerText();
  if (!bodyText.includes("Mis tareas")) {
    throw new Error(`La página abierta no parece la lista de tareas de Yarig.ai (${title})`);
  }
  return extractTasksFromText(bodyText);
}

async function syncOnce() {
  const activePage = await ensureBrowser();
  const tasks = await fetchVisibleTasks(activePage);
  const current = await api("/api/council/yar-context");
  const payload = {
    focus: current.focus || "",
    doing: current.doing || "",
    done: Array.isArray(current.done) ? current.done : [],
    tasks,
    pending: tasks,
    ask: current.ask || "",
  };
  const saved = await api("/api/council/yar-context", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  log(`Sincronizadas ${tasks.length} tareas desde Yarig.ai`);
  return saved;
}

async function main() {
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
