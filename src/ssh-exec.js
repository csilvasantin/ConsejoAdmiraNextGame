import { execFile } from "node:child_process";
import { readFile, unlink } from "node:fs/promises";
import { hostname, tmpdir } from "node:os";
import { join } from "node:path";
import { readMachines } from "./store.js";

const LOCAL_HOSTNAME = hostname().replace(/\.local$/, "").toLowerCase();

const TIMEOUT_MS = 15_000;
const CAPTURE_DELAY_MS = 4000;

// In-memory image store: machineId/captureId → Buffer
const imageBuffers = new Map();

export function getImageBuffer(id) {
  return imageBuffers.get(id) || null;
}

function isLocalMachine(machine) {
  const host = (machine.ssh?.host || "").split(".")[0].toLowerCase();
  return host === LOCAL_HOSTNAME;
}

function execLocal(script, timeout = 10_000) {
  return new Promise((resolve) => {
    execFile("osascript", ["-e", script], { timeout }, (error, stdout) => {
      resolve({ error, stdout: stdout?.trim() || "" });
    });
  });
}

function execLocalMulti(args, timeout = 10_000) {
  return new Promise((resolve) => {
    execFile("osascript", args, { timeout }, (error, stdout) => {
      resolve({ error, stdout: stdout?.trim() || "" });
    });
  });
}

function sanitizePrompt(text) {
  return text
    .replace(/[\r\n]+/g, " ")
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"')
    .slice(0, 2000);
}

function deriveLocalHostname(machine) {
  const host = machine.ssh.host || "";
  const dot = host.indexOf(".");
  if (dot > 0) {
    return host.slice(0, dot) + ".local";
  }
  return null;
}

function buildSshArgs(machine, useLocal) {
  const args = ["-o", "ConnectTimeout=5", "-o", "BatchMode=yes"];

  if (!useLocal) {
    const conn = machine.ssh.connect_tailscale || "";
    if (conn.includes("ProxyCommand")) {
      const proxy = conn.match(/-o\s+'([^']+)'/)?.[1] || conn.match(/-o\s+"([^"]+)"/)?.[1];
      if (proxy) {
        args.push("-o", proxy);
      }
    }
  }

  const user = machine.ssh.user || "csilvasantin";
  const host = useLocal ? deriveLocalHostname(machine) : (machine.ssh.ip_tailscale || machine.ssh.host);
  args.push(`${user}@${host}`);

  return args;
}

function buildScpArgs(machine, useLocal) {
  const args = ["-o", "ConnectTimeout=5", "-o", "BatchMode=yes"];

  if (!useLocal) {
    const conn = machine.ssh.connect_tailscale || "";
    if (conn.includes("ProxyCommand")) {
      const proxy = conn.match(/-o\s+'([^']+)'/)?.[1] || conn.match(/-o\s+"([^"]+)"/)?.[1];
      if (proxy) args.push("-o", proxy);
    }
  }

  return args;
}

// Try ScreenCaptureKit screenshot via Swift on remote machine
// Returns a Buffer with the screenshot, or rejects — no disk writes
function captureScreenshot(machine, useLocal) {
  return new Promise((resolve, reject) => {
    const sshArgs = buildSshArgs(machine, useLocal);
    const swiftScript = `
import Foundation
import ScreenCaptureKit
import AppKit

let sem = DispatchSemaphore(value: 0)
Task {
    do {
        let content = try await SCShareableContent.current
        guard let display = content.displays.first else { exit(1) }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        config.width = Int(display.width)
        config.height = Int(display.height)
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        let rep = NSBitmapImageRep(cgImage: image)
        let data = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.6])!
        print(data.base64EncodedString())
    } catch { print("FAIL") }
    sem.signal()
}
sem.wait()
`.trim();

    sshArgs.push(`cat > /tmp/tw_capture.swift << 'SWIFTEOF'
${swiftScript}
SWIFTEOF
swift /tmp/tw_capture.swift && rm -f /tmp/tw_capture.swift`);

    execFile("ssh", sshArgs, { timeout: 30_000 }, (err, stdout) => {
      const b64 = stdout?.trim();
      if (err || !b64 || b64 === "FAIL") return reject(err || new Error("capture failed"));
      try { resolve(Buffer.from(b64, "base64")); } catch (e) { reject(e); }
    });
  });
}

// Fallback: capture terminal text
function captureTerminalText(machine, useLocal, appName) {
  return new Promise((resolve) => {
    const sshArgs = buildSshArgs(machine, useLocal);

    if (appName === "Terminal") {
      sshArgs.push(`osascript -e 'tell application "Terminal" to get contents of front window'`);
    } else {
      sshArgs.push(`osascript -e 'tell application "${appName}" to activate' -e 'delay 0.3' -e 'tell application "System Events" to keystroke "a" using command down' -e 'tell application "System Events" to keystroke "c" using command down' -e 'delay 0.3' -e 'return (the clipboard)'`);
    }

    execFile("ssh", sshArgs, { timeout: TIMEOUT_MS }, (error, stdout) => {
      if (error) {
        resolve(null);
      } else {
        const lines = stdout.trim().split("\n");
        const last30 = lines.slice(-30).join("\n");
        resolve(last30);
      }
    });
  });
}

// In-memory store for captures
const captures = new Map();

export function getCapture(captureId) {
  return captures.get(captureId) || null;
}

const TARGET_APPS = {
  terminal: "Terminal",
  claude: "Claude",
  codex: "Codex"
};

export async function sendPromptToMachine(machineId, prompt, target = "terminal") {
  const data = await readMachines();
  const machine = data.machines.find((m) => m.id === machineId);

  if (!machine) {
    return { ok: false, error: `Máquina '${machineId}' no encontrada` };
  }

  if (!machine.ssh?.enabled) {
    return { ok: false, error: `SSH no habilitado en '${machine.name}'` };
  }

  const safe = sanitizePrompt(prompt);
  const appName = TARGET_APPS[target] || TARGET_APPS.terminal;
  const osascriptLines = [
    `tell application "${appName}" to activate`,
    `tell application "System Events" to keystroke "${safe}"`,
    'tell application "System Events" to keystroke return'
  ];

  let result;
  let usedLocal = false;

  // If this is the local machine, run osascript directly
  if (isLocalMachine(machine)) {
    const args = osascriptLines.flatMap((line) => ["-e", line]);
    const { error } = await execLocalMulti(args);
    result = error
      ? { ok: false, error: error.message }
      : { ok: true, machine: machineId, name: machine.name };
    usedLocal = true;
  } else {
    const remoteCmd = osascriptLines.map((line) => `-e '${line}'`).join(" ");

    function tryExec(useLocalNet) {
      const sshArgs = buildSshArgs(machine, useLocalNet);
      sshArgs.push(`osascript ${remoteCmd}`);
      return new Promise((resolve) => {
        execFile("ssh", sshArgs, { timeout: TIMEOUT_MS }, (error) => {
          if (error) {
            resolve({ ok: false, error: error.message });
          } else {
            resolve({ ok: true, machine: machineId, name: machine.name });
          }
        });
      });
    }

    result = await tryExec(false);
    if (!result.ok && deriveLocalHostname(machine)) {
      result = await tryExec(true);
      usedLocal = true;
    }
  }

  if (result.ok) {
    const captureId = `${machineId}-${Date.now()}`;
    result.captureId = captureId;

    // Start capture after delay (async) — stored in memory, no disk writes
    setTimeout(async () => {
      try {
        const buf = await captureScreenshot(machine, usedLocal);
        imageBuffers.set(captureId, buf);
        captures.set(captureId, { type: "image", path: `/api/screenshots/${captureId}` });
      } catch {
        // Fallback to text capture
        const text = await captureTerminalText(machine, usedLocal, appName);
        if (text) {
          captures.set(captureId, { type: "text", text });
        }
      }

      // Prune old captures
      if (captures.size > 100) {
        const oldest = captures.keys().next().value;
        captures.delete(oldest);
      }
    }, CAPTURE_DELAY_MS);
  }

  return result;
}

function isReachable(machine) {
  return machineSnapshots.has(machine.id);
}

// Build the osascript command for approval based on target app
function buildApproveScript(appName) {
  if (appName === "Claude") {
    // Claude: activa la app y envía Ctrl+Enter — macOS da foco al diálogo modal automáticamente
    return `tell application "Claude" to activate
delay 0.4
tell application "System Events" to key code 36 using control down`;
  }
  if (appName === "Codex") {
    // Codex: send "2" + Enter to approve
    return `tell application "Codex" to activate
delay 0.3
tell application "System Events"
  keystroke "2"
  delay 0.2
  key code 36
end tell`;
  }
  // Terminal fallback
  return `tell application "Terminal" to activate
delay 0.3
tell application "System Events" to key code 36 using control down`;
}

function sendKeystroke(machine, useLocal, appName) {
  const script = buildApproveScript(appName);

  // Local machine: run directly
  if (isLocalMachine(machine)) {
    const args = script.split("\n").flatMap((line) => ["-e", line.trim()]).filter((_, i) => i % 2 === 1 ? _ !== "" : true);
    return execLocal(script).then(({ error }) => ({
      machine: machine.name, id: machine.id, ok: !error, error: error?.message
    }));
  }

  return new Promise((resolve) => {
    const sshArgs = ["-o", "ConnectTimeout=3", "-o", "BatchMode=yes"];

    if (!useLocal) {
      const conn = machine.ssh.connect_tailscale || "";
      if (conn.includes("ProxyCommand")) {
        const proxy = conn.match(/-o\s+'([^']+)'/)?.[1] || conn.match(/-o\s+"([^"]+)"/)?.[1];
        if (proxy) sshArgs.push("-o", proxy);
      }
    }

    const user = machine.ssh.user || "csilvasantin";
    const host = useLocal ? deriveLocalHostname(machine) : (machine.ssh.ip_tailscale || machine.ssh.host);
    sshArgs.push(`${user}@${host}`);

    // Build remote osascript command from script lines
    const remoteCmd = script.split("\n").map((l) => `-e '${l.trim()}'`).join(" ");
    sshArgs.push(`osascript ${remoteCmd}`);

    execFile("ssh", sshArgs, { timeout: 8_000 }, (error) => {
      resolve({ machine: machine.name, id: machine.id, ok: !error, error: error?.message });
    });
  });
}

export async function approveAll(target) {
  const data = await readMachines();
  const appName = TARGET_APPS[target] || TARGET_APPS.claude;

  // ONLY send to reachable (online) machines — skip offline immediately
  const sshEnabled = data.machines.filter((m) => m.ssh?.enabled);
  const reachable = sshEnabled.filter((m) => isReachable(m) || isLocalMachine(m));
  const unreachable = sshEnabled.filter((m) => !isReachable(m) && !isLocalMachine(m));

  const results = await Promise.allSettled(
    reachable.map(async (machine) => {
      // Try .local first (faster on LAN), then Tailscale
      if (deriveLocalHostname(machine) && !isLocalMachine(machine)) {
        const r = await sendKeystroke(machine, true, appName);
        if (r.ok) return r;
      }
      return sendKeystroke(machine, false, appName);
    })
  );

  const output = results.map((r) => r.value || { ok: false, error: "rejected" });

  // Add skipped offline machines (instant, no waiting)
  for (const m of unreachable) {
    output.push({ machine: m.name, id: m.id, ok: false, error: "offline", skipped: true });
  }

  // Trigger screenshot refresh for reachable machines (async, don't block)
  setTimeout(() => {
    Promise.allSettled(
      reachable.map((m) => captureOneSnapshot(m).then((snap) => {
        if (snap) machineSnapshots.set(m.id, { ...snap, updatedAt: new Date().toISOString() });
      }))
    );
  }, 2000);

  return output;
}

export async function approveMachine(machineId, target) {
  const data = await readMachines();
  const machine = data.machines.find((m) => m.id === machineId);
  if (!machine || !machine.ssh?.enabled) {
    return { machine: machineId, ok: false, error: "No encontrada o SSH deshabilitado" };
  }

  // Check if machine is reachable before trying
  if (!isReachable(machine) && !isLocalMachine(machine)) {
    return { machine: machine.name, id: machine.id, ok: false, error: "offline" };
  }

  const appName = TARGET_APPS[target] || TARGET_APPS.claude;

  let result;
  // Try .local first (faster)
  if (deriveLocalHostname(machine) && !isLocalMachine(machine)) {
    result = await sendKeystroke(machine, true, appName);
    if (result.ok) {
      triggerPostApproveSnapshot(machine);
      return result;
    }
  }
  result = await sendKeystroke(machine, false, appName);

  if (result.ok) {
    triggerPostApproveSnapshot(machine);
  }

  return result;
}

// Capture a fresh snapshot 2s after approval for visual feedback
function triggerPostApproveSnapshot(machine) {
  setTimeout(async () => {
    const snap = await captureOneSnapshot(machine);
    if (snap) {
      machineSnapshots.set(machine.id, { ...snap, updatedAt: new Date().toISOString() });
    }
  }, 2000);
}

// Periodic snapshots of each machine's screen
const machineSnapshots = new Map();

export function getMachineSnapshot(machineId) {
  return machineSnapshots.get(machineId) || null;
}

export async function getReachableMachines() {
  const data = await readMachines();
  return data.machines.filter((m) => m.ssh?.enabled && (isReachable(m) || isLocalMachine(m)));
}

export function getAllSnapshots() {
  const result = {};
  for (const [id, snap] of machineSnapshots) {
    result[id] = snap;
  }
  return result;
}

// Python/Quartz remote screenshot + sips resize to 960px
const PYTHON_CAPTURE_REMOTE = `cat > /tmp/tw_snap.py << 'PYEOF'
import Quartz.CoreGraphics as CG
from AppKit import NSBitmapImageRep, NSJPEGFileType
i = CG.CGWindowListCreateImage(CG.CGRectInfinite, CG.kCGWindowListOptionOnScreenOnly, CG.kCGNullWindowID, CG.kCGWindowImageDefault)
if i:
    r = NSBitmapImageRep.alloc().initWithCGImage_(i)
    d = r.representationUsingType_properties_(NSJPEGFileType, {})
    d.writeToFile_atomically_("/tmp/tw_screen.jpg", True)
    print("OK")
else:
    print("FAIL")
PYEOF
python3 /tmp/tw_snap.py 2>/dev/null && sips -Z 960 /tmp/tw_screen.jpg --out /tmp/tw_screen.jpg >/dev/null 2>&1`;

// Capture desktop screenshot for a machine, save locally
// Returns a Buffer with the screenshot, or null — no disk writes ever
async function captureDesktopScreenshot(machine) {
  if (isLocalMachine(machine)) {
    // Local: captura display 2 (pantalla Claude, vertical izquierda)
    // Escribe a /tmp, lee a buffer, borra el tmp
    const tmpPath = join(tmpdir(), `tw_snap_${machine.id}_${Date.now()}.jpg`);
    return new Promise((resolve_) => {
      execFile("launchctl", ["asuser", String(process.getuid()), "screencapture", "-D", "2", "-x", "-t", "jpg", tmpPath], { timeout: 10_000 }, (err) => {
        if (err) return resolve_(null);
        execFile("sips", ["-Z", "960", tmpPath, "--out", tmpPath], { timeout: 5_000 }, async () => {
          try {
            const buf = await readFile(tmpPath);
            resolve_(buf);
          } catch { resolve_(null); }
          finally { unlink(tmpPath).catch(() => {}); }
        });
      });
    });
  }

  // Remote: captura + base64 en el equipo remoto, decode aquí — sin SCP, sin disco local
  function attempt(useLocal) {
    return new Promise((resolve_) => {
      const sshArgs = buildSshArgs(machine, useLocal);
      sshArgs.push(
        `osascript -e 'tell application "System Events" to key code 20 using {command down, shift down, control down}' -e 'delay 1' -e 'set png to the clipboard as «class PNGf»' -e 'set f to open for access POSIX file "/tmp/tw_screen_raw.png" with write permission' -e 'write png to f' -e 'close access f' ` +
        `&& sips -Z 960 -s format jpeg /tmp/tw_screen_raw.png --out /tmp/tw_screen.jpg >/dev/null 2>&1 ` +
        `&& rm -f /tmp/tw_screen_raw.png ` +
        `&& base64 /tmp/tw_screen.jpg && rm -f /tmp/tw_screen.jpg`
      );
      execFile("ssh", sshArgs, { timeout: 20_000 }, (err, stdout) => {
        const b64 = stdout?.trim();
        if (err || !b64) return resolve_(null);
        try { resolve_(Buffer.from(b64, "base64")); } catch { resolve_(null); }
      });
    });
  }

  if (deriveLocalHostname(machine)) {
    const r = await attempt(true);
    if (r) return r;
  }
  return attempt(false);
}

// Fallback: get text description of frontmost app
async function captureTextFallback(machine) {
  const script = 'tell application "System Events"\nset frontApp to name of first process whose frontmost is true\ntry\nset winName to name of front window of first process whose frontmost is true\non error\nset winName to "sin ventana"\nend try\nreturn frontApp & " — " & winName\nend tell';

  if (isLocalMachine(machine)) {
    const { error, stdout } = await execLocal(script);
    return error ? null : stdout?.trim() || null;
  }

  const remoteCmd = `osascript -e 'tell application "System Events"' -e 'set frontApp to name of first process whose frontmost is true' -e 'try' -e 'set winName to name of front window of first process whose frontmost is true' -e 'on error' -e 'set winName to "sin ventana"' -e 'end try' -e 'return frontApp & " — " & winName' -e 'end tell'`;

  function attempt(useLocal) {
    return new Promise((resolve_) => {
      const sshArgs = buildSshArgs(machine, useLocal);
      sshArgs.push(remoteCmd);
      execFile("ssh", sshArgs, { timeout: 10_000 }, (error, stdout) => {
        resolve_(error ? null : stdout?.trim() || null);
      });
    });
  }

  if (deriveLocalHostname(machine)) {
    const r = await attempt(true);
    if (r) return r;
  }
  return attempt(false);
}

// Capture all 3 displays of local Mac Mini in visual order: left→center→right
// Displays: 2=Claude (portrait left), 1=Studio (landscape center), 3=Codex (portrait right)
const LOCAL_DISPLAYS = [
  { d: 2, key: "left",   orient: "portrait"  },  // Claude — ASUS izquierda
  { d: 1, key: "center", orient: "landscape" },  // Studio Display — centro
  { d: 3, key: "right",  orient: "portrait"  },  // Codex — ASUS derecha
];

async function captureLocalAllDisplays(machine) {
  return Promise.all(LOCAL_DISPLAYS.map(({ d }) => new Promise((resolve_) => {
    const tmpPath = join(tmpdir(), `tw_snap_${machine.id}_d${d}_${Date.now()}.jpg`);
    execFile("launchctl", ["asuser", String(process.getuid()), "screencapture", "-D", String(d), "-x", "-t", "jpg", tmpPath],
      { timeout: 10_000 }, (err) => {
        if (err) return resolve_(null);
        execFile("sips", ["-Z", "960", tmpPath, "--out", tmpPath], { timeout: 5_000 }, async () => {
          try { resolve_(await readFile(tmpPath)); }
          catch { resolve_(null); }
          finally { unlink(tmpPath).catch(() => {}); }
        });
      });
  })));
}

async function captureOneSnapshot(machine) {
  if (isLocalMachine(machine)) {
    const bufs = await captureLocalAllDisplays(machine);
    const images = [];
    const orientations = [];
    for (let i = 0; i < LOCAL_DISPLAYS.length; i++) {
      const { key, orient } = LOCAL_DISPLAYS[i];
      const imgKey = `${machine.id}-${key}`;
      if (bufs[i]) {
        imageBuffers.set(imgKey, bufs[i]);
        images.push(`/api/screenshots/${imgKey}`);
        orientations.push(orient);
      }
    }
    if (images.length > 0) return { type: "images", images, orientations };
    const text = await captureTextFallback(machine);
    return text ? { type: "text", text } : null;
  }

  // Remote: single display
  const buf = await captureDesktopScreenshot(machine);
  if (buf) {
    imageBuffers.set(machine.id, buf);
    return { type: "image", image: `/api/screenshots/${machine.id}` };
  }
  const text = await captureTextFallback(machine);
  return text ? { type: "text", text } : null;
}

export async function refreshAllSnapshots() {
  const data = await readMachines();
  // Try ALL SSH-enabled machines, not just cached-reachable
  const sshEnabled = data.machines.filter((m) => m.ssh?.enabled);
  await Promise.allSettled(
    sshEnabled.map(async (machine) => {
      // Skip recently-failed machines (retry every 2 min)
      if (shouldSkipOffline(machine.id)) return;

      // Capture screenshot + app states in parallel
      const [snap, appsRaw] = await Promise.all([
        captureOneSnapshot(machine),
        captureAllAppsState(machine)
      ]);

      if (!snap && !appsRaw && !isLocalMachine(machine)) {
        markMachineFailed(machine.id);
        return; // offline
      }
      markMachineOnline(machine.id);

      const apps = parseAppsState(appsRaw);
      const existing = machineSnapshots.get(machine.id) || {};
      machineSnapshots.set(machine.id, {
        ...existing,
        ...(snap || {}),
        claudeState: apps.claude,
        codexState: apps.codex,
        updatedAt: new Date().toISOString()
      });
    })
  );
}

// Start periodic refresh
refreshAllSnapshots();
setInterval(refreshAllSnapshots, 30_000);

export function resolveMachineName(machines, input) {
  const q = input.toLowerCase().replace(/[\s-_]+/g, "");
  return machines.find((m) => {
    const id = m.id.toLowerCase().replace(/[\s-_]+/g, "");
    const name = m.name.toLowerCase().replace(/[\s-_]+/g, "");
    return id.includes(q) || name.includes(q) || id.replace("admira", "").includes(q);
  }) || null;
}

// ─── WATCHDOG: Auto-approval system ───────────────────────────────────

const WATCHDOG_INTERVAL_MS = 15_000;

// Exact button names that mean "approve this tool use" in Claude Desktop
const CLAUDE_APPROVAL_EXACT = [
  "Allow", "Yes", "OK", "Run", "Execute", "Confirm", "Accept",
  "Permitir", "Aceptar", "Sí", "Continue", "Proceed"
];
// Claude Desktop tool-use buttons that start with action verbs
const CLAUDE_TOOL_BUTTON_VERBS = [
  "Ejecutó", "ejecutó", "Run", "Check", "Install", "Clone", "List", "Show",
  "Read", "Leyó", "leyó", "Write", "Create", "Delete", "Search", "Find",
  "archivo creado", "archivos", "comandos", "herramienta", "usó"
];
// UI-only buttons to IGNORE (not tool approvals)
const CLAUDE_IGNORE_BUTTONS = [
  "Aceptar ediciones", "Opus", "Claude", "Vista previa", "~/",
  "Sonnet", "Haiku", "contexto", "Close", "Minimize", "Zoom",
  "Cancel", "Cancelar", "Done", "Listo", "Cerrar"
];
// Codex CLI approval patterns (numbered options in terminal)
const CODEX_APPROVAL_PATTERNS = [
  /approve/i, /allow/i, /permitir/i, /deny/i, /negar/i, /y\/n/i, /\[y\]/i,
  /proceed/i, /continuar/i
];

const watchdogState = {
  enabled: false,
  perMachine: {},    // { [machineId]: { enabled, claudeCount, codexCount, lastApproval, lastTarget } }
  intervalId: null,
  log: []            // last 50 auto-approvals for debugging
};

// Track last-fail times so we don't hammer offline machines every 30s
const machineFailTimes = new Map(); // machineId → timestamp of last fail
const OFFLINE_RETRY_MS = 120_000;   // retry offline machines every 2 min

function shouldSkipOffline(machineId) {
  if (isLocalMachine({ id: machineId })) return false;
  const lastFail = machineFailTimes.get(machineId);
  if (!lastFail) return false;
  return (Date.now() - lastFail) < OFFLINE_RETRY_MS;
}

function markMachineFailed(machineId) {
  machineFailTimes.set(machineId, Date.now());
}

function markMachineOnline(machineId) {
  machineFailTimes.delete(machineId);
}

function initMachineWatchdog(machineId) {
  if (!watchdogState.perMachine[machineId]) {
    watchdogState.perMachine[machineId] = {
      enabled: true,
      claudeCount: 0,
      codexCount: 0,
      lastApproval: null,
      lastTarget: null
    };
  }
}

// Check BOTH Claude AND Codex status on a machine (not just frontmost app)
async function captureAllAppsState(machine) {
  const script = `set r to ""
tell application "System Events"
  if exists process "Claude" then
    try
      set r to r & "CLAUDE:" & (name of front window of process "Claude")
    on error
      set r to r & "CLAUDE:no-window"
    end try
  else
    set r to r & "CLAUDE:OFF"
  end if
  set r to r & "|||"
  if exists process "Codex" then
    try
      set r to r & "CODEX:" & (name of front window of process "Codex")
    on error
      set r to r & "CODEX:no-window"
    end try
  else
    set r to r & "CODEX:OFF"
  end if
end tell
return r`;

  if (isLocalMachine(machine)) {
    const { error, stdout } = await execLocal(script, 8000);
    return error ? null : stdout?.trim() || null;
  }

  const lines = script.split("\n").map((l) => `-e '${l.trim()}'`).join(" ");
  const remoteCmd = `osascript ${lines}`;

  function attempt(useLocal) {
    return new Promise((resolve_) => {
      const sshArgs = buildSshArgs(machine, useLocal);
      sshArgs.push(remoteCmd);
      execFile("ssh", sshArgs, { timeout: 10_000 }, (error, stdout) => {
        resolve_(error ? null : stdout?.trim() || null);
      });
    });
  }

  if (deriveLocalHostname(machine) && !isLocalMachine(machine)) {
    const r = await attempt(true);
    if (r) return r;
  }
  return attempt(false);
}

// Parse "CLAUDE:windowTitle|||CODEX:windowTitle" into { claude, codex } states
function parseAppsState(raw) {
  if (!raw) return { claude: null, codex: null };
  const parts = raw.split("|||");
  const result = { claude: null, codex: null };
  for (const part of parts) {
    if (part.startsWith("CLAUDE:")) {
      const val = part.slice(7).trim();
      result.claude = val === "OFF" ? null : val;
    }
    if (part.startsWith("CODEX:")) {
      const val = part.slice(6).trim();
      result.codex = val === "OFF" ? null : val;
    }
  }
  return result;
}

// Play a notification sound locally (always on Mac Mini, regardless of which machine triggered)
function playApprovalSound() {
  execFile("afplay", ["/System/Library/Sounds/Glass.aiff"], { timeout: 5000 }, () => {});
}

// Scan Claude Desktop for tool-approval buttons.
// Phase 1: fast direct scan (window/group/sheet) — covers native dialogs.
// Phase 2: WebArea scan — covers Electron webview buttons (where Claude Code approvals live).
async function detectClaudeApprovalButtons(machine) {
  const script = `tell application "System Events"
  if not (exists process "Claude") then return ""
  tell process "Claude"
    set r to ""
    set foundApproval to false
    try
      repeat with w in every window
        try
          -- Phase 1: direct window/group/sheet buttons (fast, <500ms)
          repeat with b in (every button of w)
            try
              set n to name of b
              if n is not missing value and n is not "" then
                set r to r & n & "|"
                if n is in {"Allow", "Yes", "OK", "Run", "Confirm", "Permitir", "Aceptar"} then set foundApproval to true
              end if
            end try
          end repeat
          repeat with g in (every group of w)
            repeat with b in (every button of g)
              try
                set n to name of b
                if n is not missing value and n is not "" then
                  set r to r & n & "|"
                  if n is in {"Allow", "Yes", "OK", "Run", "Confirm", "Permitir", "Aceptar"} then set foundApproval to true
                end if
              end try
            end repeat
          end repeat
          repeat with s in (every sheet of w)
            repeat with b in (every button of s)
              try
                set n to name of b
                if n is not missing value and n is not "" then
                  set r to r & n & "|"
                  if n is in {"Allow", "Yes", "OK", "Run", "Confirm", "Permitir", "Aceptar"} then set foundApproval to true
                end if
              end try
            end repeat
            repeat with g in (every group of s)
              repeat with b in (every button of g)
                try
                  set n to name of b
                  if n is not missing value and n is not "" then set r to r & n & "|"
                end try
              end repeat
            end repeat
          end repeat
          -- Phase 2: WebArea scan (Electron webview) — only if Phase 1 found nothing
          if not foundApproval then
            repeat with wa in (every UI element of w whose role is "AXWebArea")
              try
                set waElems to entire contents of wa
                repeat with e in waElems
                  try
                    if role of e is "AXButton" then
                      set n to name of e
                      if n is not missing value and n is not "" then set r to r & n & "|"
                    end if
                  end try
                end repeat
              end try
            end repeat
          end if
        end try
      end repeat
    end try
    return r
  end tell
end tell`;

  if (isLocalMachine(machine)) {
    // Phase 1 fast scan: 5s. Phase 2 WebArea scan: up to 25s total.
    const { error, stdout } = await execLocal(script, 25000);
    return error ? "" : stdout?.trim() || "";
  }

  // Remote machines: send the script over SSH (WebArea scan included)
  const lines = script.split("\n").map((l) => `-e '${l.trim()}'`).join(" ");
  function attempt(useLocal) {
    return new Promise((resolve_) => {
      const sshArgs = buildSshArgs(machine, useLocal);
      sshArgs.push(`osascript ${lines}`);
      execFile("ssh", sshArgs, { timeout: 28_000 }, (error, stdout) => {
        resolve_(error ? "" : stdout?.trim() || "");
      });
    });
  }

  if (deriveLocalHostname(machine) && !isLocalMachine(machine)) {
    const r = await attempt(true);
    if (r) return r;
  }
  return attempt(false);
}

// Check if any button text indicates a pending tool approval
function hasClaudeToolApproval(buttonsStr) {
  if (!buttonsStr) return false;
  const buttons = buttonsStr.split("|").map((b) => b.trim()).filter(Boolean);
  for (const btn of buttons) {
    if (CLAUDE_IGNORE_BUTTONS.some((ign) => btn.toLowerCase().includes(ign.toLowerCase()))) continue;
    // Exact match for known approval buttons (e.g. "Allow", "Yes")
    if (CLAUDE_APPROVAL_EXACT.some((a) => btn.toLowerCase() === a.toLowerCase())) return true;
    // Verb match for tool-description buttons
    if (CLAUDE_TOOL_BUTTON_VERBS.some((verb) => btn.includes(verb))) return true;
  }
  return false;
}

// Read text content of the Codex app to detect numbered approval prompts
async function detectCodexApproval(machine) {
  const script = `tell application "System Events"
  if not (exists process "Codex") then return ""
  tell process "Codex"
    set r to ""
    try
      set fw to front window
      repeat with ta in (every text area of fw)
        try
          set v to value of ta
          if v is not missing value then set r to r & v & "\n"
        end try
      end repeat
      repeat with sa in (every scroll area of fw)
        try
          repeat with ta in (every text area of sa)
            try
              set v to value of ta
              if v is not missing value then set r to r & v & "\n"
            end try
          end repeat
        end try
      end repeat
    end try
    return r
  end tell
end tell`;

  if (isLocalMachine(machine)) {
    const { error, stdout } = await execLocal(script, 8000);
    return error ? "" : stdout?.trim() || "";
  }

  const lines = script.split("\n").map((l) => `-e '${l.trim()}'`).join(" ");
  function attempt(useLocal) {
    return new Promise((resolve_) => {
      const sshArgs = buildSshArgs(machine, useLocal);
      sshArgs.push(`osascript ${lines}`);
      execFile("ssh", sshArgs, { timeout: 10_000 }, (error, stdout) => {
        resolve_(error ? "" : stdout?.trim() || "");
      });
    });
  }

  if (deriveLocalHostname(machine) && !isLocalMachine(machine)) {
    const r = await attempt(true);
    if (r) return r;
  }
  return attempt(false);
}

function hasCodexApproval(text) {
  if (!text) return false;
  const lower = text.toLowerCase();
  // Codex CLI shows numbered options: "1)" + approval keywords
  const hasNumbers = /\b[123][).]\s/m.test(text);
  const hasApproval = CODEX_APPROVAL_PATTERNS.some((re) => re.test(lower));
  return hasNumbers && hasApproval;
}

// Detect approval prompts by reading terminal content on a remote/local machine
async function detectTerminalApproval(machine) {
  // Read the last 30 lines of every Terminal tab to find approval prompts
  const script = `
set result to ""
tell application "Terminal"
  repeat with w in every window
    repeat with t in every tab of w
      try
        set c to contents of t
        -- Get last 800 chars (where the prompt would be)
        set cLen to length of c
        if cLen > 800 then
          set c to text (cLen - 800) thru cLen of c
        end if
        -- Check for Claude Code approval patterns
        if c contains "Do you want to proceed?" or c contains "Allow" or c contains "allow this" or c contains "Tool Use" or c contains "wants to" or c contains "Approve" or c contains "approve" or c contains "Y/n" or c contains "y/N" or c contains "Accept" or c contains "permit" then
          set result to result & "CLAUDE_TERM:PENDING|"
        end if
        -- Check for Codex approval patterns (numbered options)
        if c contains "1)" and c contains "2)" and (c contains "approve" or c contains "Allow" or c contains "always" or c contains "deny" or c contains "Deny" or c contains "Skip" or c contains "skip") then
          set result to result & "CODEX_TERM:PENDING|"
        end if
      end try
    end repeat
  end repeat
end tell
return result`;

  if (isLocalMachine(machine)) {
    const { error, stdout } = await execLocal(script, 10000);
    return error ? "" : stdout?.trim() || "";
  }

  const lines = script.split("\n").map((l) => `-e '${l.trim()}'`).filter(l => l !== "-e ''").join(" ");
  function attempt(useLocal) {
    return new Promise((resolve_) => {
      const sshArgs = buildSshArgs(machine, useLocal);
      sshArgs.push(`osascript ${lines}`);
      execFile("ssh", sshArgs, { timeout: 12_000 }, (error, stdout) => {
        resolve_(error ? "" : stdout?.trim() || "");
      });
    });
  }

  if (deriveLocalHostname(machine) && !isLocalMachine(machine)) {
    const r = await attempt(true);
    if (r) return r;
  }
  return attempt(false);
}

async function watchdogCheck() {
  if (!watchdogState.enabled) return;

  const data = await readMachines();
  // Try ALL SSH-enabled machines — not just cached-reachable ones.
  // When an offline machine comes back, we'll detect it and start monitoring.
  const machines = data.machines.filter((m) => m.ssh?.enabled);

  await Promise.allSettled(
    machines.map(async (machine) => {
      initMachineWatchdog(machine.id);
      const mState = watchdogState.perMachine[machine.id];
      if (!mState.enabled) return;

      // Skip recently-failed (offline) machines to avoid blocking the cycle
      if (shouldSkipOffline(machine.id)) return;

      // Check GUI app states (window titles)
      const raw = await captureAllAppsState(machine);
      if (!raw && !isLocalMachine(machine)) {
        markMachineFailed(machine.id);
        return; // machine unreachable, skip rest
      }
      markMachineOnline(machine.id); // machine responded!
      const apps = parseAppsState(raw);
      mState.claudeState = apps.claude;
      mState.codexState = apps.codex;

      let claudeApproved = false;
      let codexApproved = false;

      // --- CLAUDE DESKTOP DETECTION ---
      if (apps.claude && apps.claude !== "no-window") {
        const buttonsStr = await detectClaudeApprovalButtons(machine);
        mState.claudeButtons = buttonsStr;
        if (hasClaudeToolApproval(buttonsStr)) {
          playApprovalSound();
          await autoApprove(machine, "claude", mState);
          claudeApproved = true;
        }
      }

      // --- CODEX DETECTION ---
      if (apps.codex && apps.codex !== "OFF") {
        // 1. Check window title (fast, catches obvious cases)
        const codexTitle = (apps.codex || "").toLowerCase();
        const titleHasApproval = ["approve", "aprobar", "confirm", "confirmar",
          "accept", "aceptar", "permission", "permiso", "waiting", "esperando",
          "y/n", "allow", "permitir"].some((kw) => codexTitle.includes(kw));
        if (titleHasApproval) {
          playApprovalSound();
          await autoApprove(machine, "codex", mState);
          codexApproved = true;
        } else {
          // 2. Read Codex app text content for numbered approval options
          const codexText = await detectCodexApproval(machine);
          if (hasCodexApproval(codexText)) {
            playApprovalSound();
            await autoApprove(machine, "codex", mState);
            codexApproved = true;
          }
        }
      }

      // --- TERMINAL DETECTION (Claude Code CLI / Codex CLI) ---
      // Only check Terminal if we haven't already approved via Desktop apps
      if (!claudeApproved || !codexApproved) {
        const termResult = await detectTerminalApproval(machine);
        mState.terminalState = termResult; // debug
        if (!claudeApproved && termResult.includes("CLAUDE_TERM:PENDING")) {
          playApprovalSound();
          await autoApprove(machine, "terminal_claude", mState);
        }
        if (!codexApproved && termResult.includes("CODEX_TERM:PENDING")) {
          playApprovalSound();
          await autoApprove(machine, "codex", mState);
        }
      }
    })
  );
}

const lastApprovalTimes = new Map(); // `${machineId}:${target}` → timestamp
const APPROVAL_COOLDOWN_MS = 12_000; // don't re-approve same target within 12s

async function autoApprove(machine, target, mState) {
  // Cooldown: avoid double-approving while dialog is still clearing
  const cooldownKey = `${machine.id}:${target}`;
  const lastTime = lastApprovalTimes.get(cooldownKey) || 0;
  if (Date.now() - lastTime < APPROVAL_COOLDOWN_MS) return;
  lastApprovalTimes.set(cooldownKey, Date.now());

  // terminal_claude uses Terminal app with Ctrl+Enter
  const effectiveTarget = target === "terminal_claude" ? "terminal" : target;
  const appName = TARGET_APPS[effectiveTarget] || TARGET_APPS.claude;
  let result;
  if (deriveLocalHostname(machine) && !isLocalMachine(machine)) {
    result = await sendKeystroke(machine, true, appName);
    if (!result.ok) result = await sendKeystroke(machine, false, appName);
  } else {
    result = await sendKeystroke(machine, false, appName);
  }

  if (result.ok) {
    if (target === "claude" || target === "terminal_claude") mState.claudeCount++;
    else if (target === "codex") mState.codexCount++;
    mState.lastApproval = new Date().toISOString();
    mState.lastTarget = target;

    watchdogState.log.push({
      machine: machine.name,
      machineId: machine.id,
      target,
      at: mState.lastApproval
    });
    if (watchdogState.log.length > 50) watchdogState.log.shift();

    triggerPostApproveSnapshot(machine);
  }
}

export function startWatchdog() {
  if (watchdogState.intervalId) return;
  watchdogState.enabled = true;
  watchdogState.intervalId = setInterval(watchdogCheck, WATCHDOG_INTERVAL_MS);
  // Run immediately
  watchdogCheck();
}

export function stopWatchdog() {
  watchdogState.enabled = false;
  if (watchdogState.intervalId) {
    clearInterval(watchdogState.intervalId);
    watchdogState.intervalId = null;
  }
}

export function setWatchdogEnabled(enabled) {
  if (enabled) startWatchdog();
  else stopWatchdog();
}

export function setMachineWatchdog(machineId, enabled) {
  initMachineWatchdog(machineId);
  watchdogState.perMachine[machineId].enabled = enabled;
}

export function getWatchdogState() {
  return {
    enabled: watchdogState.enabled,
    perMachine: watchdogState.perMachine,
    log: watchdogState.log.slice(-20)
  };
}
