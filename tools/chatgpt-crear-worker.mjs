#!/usr/bin/env node

import os from "node:os";
import path from "node:path";
import fs from "node:fs/promises";
import { chromium } from "playwright-core";

const API_BASE_URL = process.env.COUNCIL_API_BASE_URL || "https://three2-consejoadmiranextgame.onrender.com";
const API_TOKEN = process.env.COUNCIL_API_TOKEN || "admira2026";
const WORKER_ID = process.env.COUNCIL_CREAR_WORKER_ID || `${os.hostname()}-${process.pid}`;
const POLL_MS = Number(process.env.COUNCIL_CREAR_POLL_MS || 10000);
const ONCE = process.argv.includes("--once");
const KEEP_OPEN = process.argv.includes("--keep-open");

const CHATGPT_URL = process.env.CHATGPT_URL || "https://chatgpt.com";
const CHATGPT_TIMEOUT_MS = Number(process.env.CHATGPT_TIMEOUT_MS || 180000);
const CHROME_EXECUTABLE =
  process.env.CHATGPT_CHROME_EXECUTABLE ||
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const CHROME_USER_DATA_DIR =
  process.env.CHATGPT_CHROME_USER_DATA_DIR ||
  path.join(os.homedir(), "Library/Application Support/Google/Chrome");
const CHROME_PROFILE_DIR = process.env.CHATGPT_CHROME_PROFILE_DIR || "Profile 1";
const CHROME_AUTOMATION_USER_DATA_DIR =
  process.env.CHATGPT_AUTOMATION_USER_DATA_DIR ||
  path.join(os.homedir(), "Library/Application Support/Google/Chrome-CouncilCrear-Profile1");

let context = null;
let page = null;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function log(message, extra = null) {
  const stamp = new Date().toISOString();
  if (extra == null) {
    console.log(`[${stamp}] ${message}`);
  } else {
    console.log(`[${stamp}] ${message}`, extra);
  }
}

function buildPrompt(job) {
  const hints = [];
  if (job.calidad === "standard-square") hints.push("Formato 1:1.");
  if (job.calidad === "standard-wide") hints.push("Formato apaisado 16:9.");
  if (job.calidad === "hd-square") hints.push("Formato 1:1 y acabado de alta calidad.");
  if (job.calidad === "hd-wide") hints.push("Formato apaisado 16:9 y acabado de alta calidad.");
  if (job.gen === "coetaneos") hints.push("Estética contemporánea, limpia y actual.");
  if (job.gen === "leyendas") hints.push("Estética icónica, con aura de clásico atemporal.");
  return [job.prompt.trim(), hints.join(" ")].filter(Boolean).join("\n\n");
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

async function claimJob() {
  try {
    const data = await api("/api/council/crear/claim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workerId: WORKER_ID }),
    });
    return data.job || null;
  } catch (error) {
    if (!/HTTP 404|HTTP 405/.test(String(error.message || ""))) {
      throw error;
    }
    const legacy = await api("/api/council/crear-pending");
    return (legacy.jobs && legacy.jobs[0]) || null;
  }
}

async function reportResult(jobId, imageUrl) {
  return api(`/api/council/crear/${encodeURIComponent(jobId)}/result`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ imageUrl }),
  });
}

async function reportError(jobId, error) {
  return api(`/api/council/crear/${encodeURIComponent(jobId)}/error`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ error: String(error || "Error desconocido").slice(0, 500) }),
  });
}

async function launchChromeContext(userDataDir) {
  return chromium.launchPersistentContext(userDataDir, {
    executablePath: CHROME_EXECUTABLE,
    channel: undefined,
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

async function ensureBrowser() {
  if (page) return page;
  try {
    context = await launchChromeContext(CHROME_USER_DATA_DIR);
  } catch (error) {
    if (!String(error.message || "").includes("ProcessSingleton")) {
      throw error;
    }
    try {
      await fs.access(path.join(CHROME_AUTOMATION_USER_DATA_DIR, CHROME_PROFILE_DIR));
      log("Perfil principal en uso; reutilizando perfil persistente de automatización");
    } catch {
      log("Perfil principal en uso; creando perfil persistente de automatización para el worker");
      await cloneChromeProfile(CHROME_AUTOMATION_USER_DATA_DIR);
    }
    context = await launchChromeContext(CHROME_AUTOMATION_USER_DATA_DIR);
  }
  page = context.pages()[0] || await context.newPage();
  page.setDefaultTimeout(15000);
  return page;
}

async function ensureChatgptReady(activePage) {
  await activePage.goto(CHATGPT_URL, { waitUntil: "domcontentloaded" });
  await activePage.waitForLoadState("domcontentloaded");
  const url = activePage.url();
  if (url.includes("/auth/login") || url.includes("accounts.google.com") || url.includes("auth.openai.com")) {
    throw new Error(
      `ChatGPT no tiene sesión reutilizable en el perfil de automatización (${CHROME_AUTOMATION_USER_DATA_DIR}). Abre la ventana lanzada por el worker, inicia sesión una vez y vuelve a arrancarlo.`
    );
  }

  const composer = activePage.locator('textarea, div[contenteditable=\"true\"]');
  await composer.first().waitFor({ state: "visible", timeout: 30000 });
}

async function snapshotImageCandidates(activePage) {
  const images = await activePage.locator("main img").evaluateAll((nodes) =>
    nodes
      .map((img, index) => ({
        index,
        src: img.currentSrc || img.src || "",
        alt: img.getAttribute("alt") || "",
        width: img.naturalWidth || img.clientWidth || 0,
        height: img.naturalHeight || img.clientHeight || 0,
      }))
      .filter((img) => img.src && img.width >= 256 && img.height >= 256)
  );
  return images;
}

async function submitPrompt(activePage, prompt) {
  const editableSelector = 'div[contenteditable="true"]';
  const editable = activePage.locator(editableSelector).first();
  const editableCount = await activePage.locator(editableSelector).count();
  if (editableCount > 0) {
    await editable.waitFor({ state: "visible", timeout: 30000 });
    await editable.click();
    await editable.fill(prompt);
    await editable.press("Enter");
    return;
  }

  const composer = activePage.locator("textarea").first();
  await composer.waitFor({ state: "visible", timeout: 30000 });
  await composer.click();
  await composer.fill(prompt);
  await composer.press("Enter");
}

async function waitForGeneratedImage(activePage, beforeImages) {
  const beforeSrcs = new Set(beforeImages.map((img) => img.src));
  const startedAt = Date.now();

  while ((Date.now() - startedAt) < CHATGPT_TIMEOUT_MS) {
    const images = await snapshotImageCandidates(activePage);
    const fresh = images.filter((img) => !beforeSrcs.has(img.src));
    if (fresh.length > 0) {
      fresh.sort((a, b) => (b.width * b.height) - (a.width * a.height));
      return fresh[0];
    }
    await sleep(2500);
  }

  throw new Error("ChatGPT no devolvió una imagen nueva dentro del tiempo límite");
}

async function extractImageBytes(activePage, imageSrc) {
  const result = await activePage.evaluate(async (src) => {
    const res = await fetch(src);
    const blob = await res.blob();
    const buffer = await blob.arrayBuffer();
    let binary = "";
    const bytes = new Uint8Array(buffer);
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return {
      mimeType: blob.type || "image/png",
      base64: btoa(binary),
    };
  }, imageSrc);
  return {
    mimeType: result.mimeType,
    buffer: Buffer.from(result.base64, "base64"),
  };
}

async function uploadToCatbox(localPath) {
  const bytes = await fs.readFile(localPath);
  const form = new FormData();
  form.set("reqtype", "fileupload");
  form.set("fileToUpload", new Blob([bytes]), path.basename(localPath));
  const res = await fetch("https://catbox.moe/user/api.php", {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    throw new Error(`Catbox HTTP ${res.status}`);
  }
  const text = (await res.text()).trim();
  if (!/^https?:\/\//.test(text)) {
    throw new Error(`Catbox respondió algo inesperado: ${text.slice(0, 200)}`);
  }
  return text;
}

async function processJob(job) {
  log(`Reclamado job ${job.id}: ${job.prompt}`);
  const activePage = await ensureBrowser();
  await ensureChatgptReady(activePage);

  const prompt = buildPrompt(job);
  const beforeImages = await snapshotImageCandidates(activePage);
  await submitPrompt(activePage, prompt);
  log(`Prompt enviado a ChatGPT para job ${job.id}`);

  const generated = await waitForGeneratedImage(activePage, beforeImages);
  log(`Imagen detectada para job ${job.id}`, generated);

  const image = await extractImageBytes(activePage, generated.src);
  const ext = image.mimeType.includes("jpeg") ? "jpg" : "png";
  const localPath = path.join(os.tmpdir(), `council-crear-${job.id}.${ext}`);
  await fs.writeFile(localPath, image.buffer);

  const publicUrl = await uploadToCatbox(localPath);
  log(`Imagen subida: ${publicUrl}`);
  await reportResult(job.id, publicUrl);
  log(`Job ${job.id} completado`);
}

async function main() {
  log(`Worker Crear listo como ${WORKER_ID}`);
  log(`API: ${API_BASE_URL}`);
  log(`Chrome profile: ${CHROME_USER_DATA_DIR} [${CHROME_PROFILE_DIR}]`);
  log(`Chrome automation profile: ${CHROME_AUTOMATION_USER_DATA_DIR}`);

  do {
    let job = null;
    try {
      job = await claimJob();
    } catch (error) {
      log("No se pudo reclamar job", error.message);
      if (ONCE) break;
      await sleep(POLL_MS);
      continue;
    }

    if (!job) {
      if (ONCE) break;
      await sleep(POLL_MS);
      continue;
    }

    try {
      await processJob(job);
    } catch (error) {
      log(`Fallo job ${job.id}`, error.message);
      try {
        await reportError(job.id, error.message);
      } catch (reportingError) {
        log(`No se pudo reportar el fallo del job ${job.id}`, reportingError.message);
      }
      if (!KEEP_OPEN) await closeBrowser();
    }
  } while (!ONCE);
}

main().catch(async (error) => {
  console.error(error);
  process.exitCode = 1;
  await closeBrowser();
});
