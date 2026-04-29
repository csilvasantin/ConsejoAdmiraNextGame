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
const WATCH_AFTER_LOGIN = process.argv.includes("--watch-after-login");
const TASK_ACTION_INDEX = process.argv.indexOf("--task-action");
const TASK_ACTION = TASK_ACTION_INDEX >= 0 ? String(process.argv[TASK_ACTION_INDEX + 1] || "").trim().toLowerCase() : "";
const POLL_MS = Number(process.env.YARIG_SYNC_POLL_MS || 60000);
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
const SNAPSHOT_PATH =
  process.env.YARIG_SNAPSHOT_PATH ||
  path.join(os.homedir(), "Library/Logs/council-api/yarig-last.json");

let context = null;
let page = null;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function log(message, extra = null) {
  if (DUMP_JSON || TASK_ACTION) return;
  const stamp = new Date().toISOString();
  if (extra == null) console.log(`[${stamp}] ${message}`);
  else console.log(`[${stamp}] ${message}`, extra);
}

async function saveSnapshot(payload) {
  const snapshot = {
    savedAt: new Date().toISOString(),
    ...payload,
  };
  await fs.mkdir(path.dirname(SNAPSHOT_PATH), { recursive: true });
  await fs.writeFile(SNAPSHOT_PATH, JSON.stringify(snapshot, null, 2), "utf8");
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

async function ensureAutomationProfileSeeded() {
  try {
    await fs.access(path.join(CHROME_AUTOMATION_USER_DATA_DIR, CHROME_PROFILE_DIR));
  } catch {
    log("Creando perfil persistente de automatización para Yarig");
    await cloneChromeProfile(CHROME_AUTOMATION_USER_DATA_DIR);
  }
}

async function ensureBrowser() {
  if (page) return page;
  await ensureAutomationProfileSeeded();
  try {
    context = await launchChromeContext(CHROME_AUTOMATION_USER_DATA_DIR);
  } catch (error) {
    throw error;
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

async function extractTaskBucketsFromDom(activePage) {
  return activePage.evaluate(() => {
    const rootText = document.body?.innerText || "";
    const emailRe = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/ig;
    const dateRe = /Tarea añadida el \d{2}\/\d{2}\/\d{4}/;
    const descRe = /Descripción:\s*([^\n]+)/i;
    const statusPatterns = [
      { label: "En proceso", re: /\bEn proceso\b/i },
      { label: "Pendiente", re: /\bPendiente\b/i },
      { label: "Finalizada", re: /\bFinalizada\b/i },
      { label: "Finalizado", re: /\bFinalizado\b/i },
    ];
    const attrTexts = [];
    document.querySelectorAll("[title],[aria-label],[alt]").forEach((el) => {
      ["title", "aria-label", "alt"].forEach((attr) => {
        const value = el.getAttribute(attr);
        if (value) attrTexts.push(value);
      });
    });
    const loginUser = ((rootText + "\n" + attrTexts.join("\n")).match(emailRe) || [])[0] || "";
    const candidates = Array.from(document.querySelectorAll("div, article, section, li"))
      .filter((el) => {
        const text = el.innerText || "";
        return dateRe.test(text) && descRe.test(text);
      })
      .filter((el) => {
        return !Array.from(el.children || []).some((child) => {
          const text = child.innerText || "";
          return dateRe.test(text) && descRe.test(text);
        });
      });
    const seen = new Set();
    const activeTasks = [];
    const doneTasks = [];
    for (const el of candidates) {
      const text = (el.innerText || "").replace(/\r/g, "").trim();
      if (!text || seen.has(text)) continue;
      seen.add(text);
      const desc = text.match(descRe)?.[1]?.trim();
      if (!desc) continue;
      const status = (statusPatterns.find(({ re }) => re.test(text))?.label) || "Pendiente";
      const line = `${status} - ${desc}`.slice(0, 240);
      if (/^Finalizad[ao]$/i.test(status)) doneTasks.push(line);
      else activeTasks.push(line);
    }
    return {
      tasks: activeTasks.slice(0, 12),
      done: doneTasks.slice(0, 12),
      loginUser,
    };
  });
}

async function inspectCurrentPage(activePage) {
  const title = await activePage.title();
  const url = activePage.url();
  if (/login|auth/i.test(url) || !/(^|\.)yarig\.ai/i.test(new URL(url).hostname)) {
    throw new Error(`Yarig.ai no está accesible en sesión reutilizable: ${url}`);
  }
  const bodyText = await activePage.locator("body").innerText();
  if (!bodyText.includes("Mis tareas")) {
    throw new Error(`La página abierta no parece la lista de tareas de Yarig.ai (${title})`);
  }
  const domPayload = await extractTaskBucketsFromDom(activePage);
  return {
    ...((domPayload.tasks.length || domPayload.done.length) ? domPayload : extractTaskBucketsFromText(bodyText)),
    currentUrl: url,
    title,
    loginUser: domPayload.loginUser || "",
  };
}

async function fetchVisibleTasks(activePage, opts = {}) {
  if (opts.preferCurrent) {
    try {
      return await inspectCurrentPage(activePage);
    } catch {}
  }
  await activePage.goto(YARIG_URL, { waitUntil: "domcontentloaded" });
  await activePage.waitForLoadState("domcontentloaded");
  return inspectCurrentPage(activePage);
}

function normalizeTaskLine(line) {
  return String(line || "").replace(/\s+/g, " ").trim();
}

function taskDescriptionFromLine(line) {
  const cleaned = normalizeTaskLine(line);
  const idx = cleaned.indexOf(" - ");
  return idx >= 0 ? cleaned.slice(idx + 3).trim() : cleaned;
}

async function findCurrentTaskCard(activePage, taskHint = "") {
  const controlButton = activePage.getByRole("button", { name: /control tarea/i });
  let card = activePage.locator("div,article,section").filter({
    hasText: /En proceso/i,
    has: controlButton,
  }).first();
  if (taskHint) {
    const hinted = activePage.locator("div,article,section").filter({
      hasText: taskHint,
      has: controlButton,
    }).first();
    if (await hinted.count()) card = hinted;
  }
  await card.waitFor({ state: "visible", timeout: 15000 });
  return card;
}

async function openTaskControlModal(activePage, taskHint = "") {
  const card = await findCurrentTaskCard(activePage, taskHint);
  await card.getByRole("button", { name: /control tarea/i }).click();
  await activePage.waitForTimeout(1200);
  return card;
}

async function pickModalControlButtons(activePage) {
  const buttons = await activePage.evaluate(() => {
    const viewportWidth = window.innerWidth || 0;
    const viewportHeight = window.innerHeight || 0;
    return Array.from(document.querySelectorAll("button"))
      .map((button) => {
        const rect = button.getBoundingClientRect();
        const label = [button.innerText || "", button.getAttribute("title") || "", button.getAttribute("aria-label") || ""].join(" ").trim();
        return {
          label,
          rect: {
            x: rect.x,
            y: rect.y,
            w: rect.width,
            h: rect.height,
          },
          area: rect.width * rect.height,
          centerX: rect.x + (rect.width / 2),
          centerY: rect.y + (rect.height / 2),
          visible: rect.width > 30 && rect.height > 30,
          viewportWidth,
          viewportHeight,
        };
      })
      .filter((item) => item.visible)
      .filter((item) => !/control tarea/i.test(item.label))
      .filter((item) => !/minimizar/i.test(item.label))
      .filter((item) => item.centerY < item.viewportHeight * 0.7)
      .filter((item) => item.area > 2000)
      .sort((a, b) => a.centerX - b.centerX);
  });
  if (!buttons.length) {
    throw new Error("No pude localizar los controles visuales de la tarea en Yarig.ai");
  }
  return buttons;
}

async function performTaskAction(activePage, action, taskHint = "") {
  if (!["pause", "cancel", "finalize"].includes(action)) {
    throw new Error(`Acción de Yarig no soportada: ${action}`);
  }
  await openTaskControlModal(activePage, taskHint);
  const controls = await pickModalControlButtons(activePage);
  const left = controls[0];
  const right = controls[controls.length - 1];
  const center = controls.reduce((best, item) => (item.area > best.area ? item : best), controls[0]);
  let x = center.centerX;
  let y = center.centerY;
  if (action === "cancel") {
    x = left.centerX;
    y = left.centerY;
  } else if (action === "finalize") {
    x = right.centerX;
    y = right.centerY;
  } else {
    x = center.rect.x + (center.rect.w * 0.30);
    y = center.centerY;
  }
  await activePage.mouse.click(x, y);
  await activePage.waitForTimeout(2200);
}

async function syncOnce() {
  const activePage = await ensureBrowser();
  const livePayload = await fetchVisibleTasks(activePage, { preferCurrent: true });
  return syncPayloadToApi(livePayload, activePage);
}

async function syncPayloadToApi(livePayload, activePage = null) {
  const { tasks, done, loginUser } = livePayload;
  const current = await api("/api/council/yar-context");
  const payload = {
    focus: current.focus || "",
    doing: current.doing || "",
    done,
    tasks,
    pending: tasks,
    ask: current.ask || "",
    syncUser: loginUser || current.syncUser || "",
  };
  const saved = await api("/api/council/yar-context", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await saveSnapshot({
    tasks,
    done,
    currentUrl: livePayload.currentUrl || activePage?.url?.() || "",
    title: livePayload.title || (activePage ? await activePage.title() : ""),
    source: "worker-sync",
    loginUser: loginUser || "",
  });
  log(`Sincronizadas ${tasks.length} tareas activas y ${done.length} finalizadas desde Yarig.ai`);
  return saved;
}

async function runTaskAction(action) {
  const activePage = await ensureBrowser();
  const currentPayload = await fetchVisibleTasks(activePage, { preferCurrent: true });
  const currentTask = (currentPayload.tasks || []).find((item) => /^En proceso\b/i.test(normalizeTaskLine(item)));
  if (!currentTask) {
    throw new Error("No hay ninguna tarea en proceso en Yarig.ai para controlar");
  }
  await performTaskAction(activePage, action, taskDescriptionFromLine(currentTask));
  const refreshedPayload = await fetchVisibleTasks(activePage, { preferCurrent: true });
  const saved = await syncPayloadToApi(refreshedPayload, activePage);
  return {
    ok: true,
    action,
    currentTask,
    currentUrl: refreshedPayload.currentUrl,
    title: refreshedPayload.title,
    tasks: refreshedPayload.tasks,
    done: refreshedPayload.done,
    context: saved,
  };
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
      const payload = await inspectCurrentPage(activePage);
      await saveSnapshot({
        tasks: payload.tasks,
        done: payload.done,
        currentUrl: payload.currentUrl,
        title: payload.title,
        source: "prepare-login",
        loginUser: payload.loginUser || "",
      });
      log(`Sesion de Yarig.ai lista con ${payload.tasks.length} tareas activas y ${payload.done.length} finalizadas`);
      return payload;
    } catch (error) {
      lastError = error;
      await sleep(1000);
    }
  }
  throw lastError || new Error("Yarig login no se completo a tiempo");
}

async function watchLoop(activePage) {
  log(`Watcher de Yarig.ai activo cada ${Math.round(POLL_MS / 1000)}s`);
  do {
    try {
      const livePayload = await fetchVisibleTasks(activePage, { preferCurrent: true });
      await syncPayloadToApi(livePayload, activePage);
    } catch (error) {
      log("Fallo del watcher Yarig.ai", error.message);
    }
    await sleep(POLL_MS);
  } while (true);
}

async function main() {
  if (TASK_ACTION) {
    const payload = await runTaskAction(TASK_ACTION);
    process.stdout.write(JSON.stringify(payload));
    await closeBrowser();
    return;
  }
  if (WATCH_AFTER_LOGIN) {
    const payload = await prepareLoginWindow();
    process.stdout.write(JSON.stringify({
      ok: true,
      prepared: true,
      watching: true,
      currentUrl: payload.currentUrl,
      title: payload.title,
      tasks: payload.tasks,
      done: payload.done,
    }));
    await syncPayloadToApi(payload, page);
    await watchLoop(page);
    return;
  }
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
    const payload = await fetchVisibleTasks(activePage, { preferCurrent: true });
    await saveSnapshot({
      tasks: payload.tasks,
      done: payload.done,
      currentUrl: payload.currentUrl,
      title: payload.title,
      source: "dump-json",
      loginUser: payload.loginUser || "",
    });
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
