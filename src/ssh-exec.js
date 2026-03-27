import { execFile } from "node:child_process";
import { resolve } from "node:path";
import { mkdir } from "node:fs/promises";
import { hostname } from "node:os";
import { readMachines } from "./store.js";

const LOCAL_HOSTNAME = hostname().replace(/\.local$/, "").toLowerCase();

const TIMEOUT_MS = 15_000;
const CAPTURE_DELAY_MS = 4000;
const SCREENSHOTS_DIR = resolve(import.meta.dirname, "../data/screenshots");

// Ensure screenshots dir exists
mkdir(SCREENSHOTS_DIR, { recursive: true }).catch(() => {});

function isLocalMachine(machine) {
  const host = (machine.ssh.host || "").split(".")[0].toLowerCase();
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
function captureScreenshot(machine, useLocal, localPath) {
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
        try data.write(to: URL(fileURLWithPath: "/tmp/tw_screen.jpg"))
        print("OK")
    } catch { print("FAIL") }
    sem.signal()
}
sem.wait()
`.trim();

    // Write swift script and execute
    sshArgs.push(`cat > /tmp/tw_capture.swift << 'SWIFTEOF'
${swiftScript}
SWIFTEOF
swift /tmp/tw_capture.swift`);

    execFile("ssh", sshArgs, { timeout: 30_000 }, (err, stdout) => {
      if (err || !stdout.includes("OK")) return reject(err || new Error("capture failed"));

      // SCP the file back
      const user = machine.ssh.user || "csilvasantin";
      const host = useLocal
        ? deriveLocalHostname(machine)
        : (machine.ssh.ip_tailscale || machine.ssh.host);
      const scpArgs = buildScpArgs(machine, useLocal);
      scpArgs.push(`${user}@${host}:/tmp/tw_screen.jpg`, localPath);

      execFile("scp", scpArgs, { timeout: TIMEOUT_MS }, (scpErr) => {
        if (scpErr) return reject(scpErr);
        resolve(localPath);
      });
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

    // Start capture after delay (async)
    setTimeout(async () => {
      // Try screenshot first
      const screenshotPath = resolve(SCREENSHOTS_DIR, `${captureId}.jpg`);
      try {
        await captureScreenshot(machine, usedLocal, screenshotPath);
        captures.set(captureId, { type: "image", path: `/api/screenshots/${captureId}.jpg` });
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

function sendKeystroke(machine, useLocal, appName, keyCmd) {
  // Local machine: run directly
  if (isLocalMachine(machine)) {
    return execLocalMulti(["-e", `tell application "${appName}" to activate`, "-e", "delay 0.3", "-e", keyCmd]).then(({ error }) => ({
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
    sshArgs.push(`osascript -e 'tell application "${appName}" to activate' -e 'delay 0.3' -e '${keyCmd}'`);

    execFile("ssh", sshArgs, { timeout: 8_000 }, (error) => {
      resolve({ machine: machine.name, id: machine.id, ok: !error, error: error?.message });
    });
  });
}

export async function approveAll(target) {
  const data = await readMachines();
  const appName = TARGET_APPS[target] || TARGET_APPS.claude;
  const keyCmd = 'tell application "System Events" to key code 36 using control down';

  // Send to all reachable machines including this one (macmini)
  const sshEnabled = data.machines.filter((m) => m.ssh?.enabled);
  const reachable = sshEnabled.filter((m) => isReachable(m) || m.id === "admira-macmini");
  const unreachable = sshEnabled.filter((m) => !isReachable(m) && m.id !== "admira-macmini");

  const results = await Promise.allSettled(
    reachable.map(async (machine) => {
      // Try .local first (faster on LAN), then Tailscale
      if (deriveLocalHostname(machine)) {
        const r = await sendKeystroke(machine, true, appName, keyCmd);
        if (r.ok) return r;
      }
      return sendKeystroke(machine, false, appName, keyCmd);
    })
  );

  const output = results.map((r) => r.value || { ok: false, error: "rejected" });

  // Add skipped machines
  for (const m of unreachable) {
    output.push({ machine: m.name, id: m.id, ok: false, error: "offline" });
  }

  return output;
}

export async function approveMachine(machineId, target) {
  const data = await readMachines();
  const machine = data.machines.find((m) => m.id === machineId);
  if (!machine || !machine.ssh?.enabled) {
    return { machine: machineId, ok: false, error: "No encontrada o SSH deshabilitado" };
  }

  const appName = TARGET_APPS[target] || TARGET_APPS.claude;
  const keyCmd = 'tell application "System Events" to key code 36 using control down';

  // Try .local first (faster)
  if (deriveLocalHostname(machine)) {
    const r = await sendKeystroke(machine, true, appName, keyCmd);
    if (r.ok) return r;
  }
  return sendKeystroke(machine, false, appName, keyCmd);
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

async function captureOneSnapshot(machine) {
  const snapshotScript = 'try\ntell application "Terminal" to get contents of front window\non error\ntell application "System Events"\nset frontApp to name of first process whose frontmost is true\ntry\nset winName to name of front window of first process whose frontmost is true\non error\nset winName to "sin ventana"\nend try\nreturn frontApp & " — " & winName\nend tell\nend try';

  function parseOutput(stdout) {
    const text = stdout?.trim();
    if (!text) return null;
    return text.split("\n").slice(-20).join("\n");
  }

  // Local machine: run directly
  if (isLocalMachine(machine)) {
    const { error, stdout } = await execLocal(snapshotScript);
    return error ? null : parseOutput(stdout);
  }

  const remoteScript = `osascript \
-e 'try' \
-e 'tell application "Terminal" to get contents of front window' \
-e 'on error' \
-e 'tell application "System Events"' \
-e 'set frontApp to name of first process whose frontmost is true' \
-e 'try' \
-e 'set winName to name of front window of first process whose frontmost is true' \
-e 'on error' \
-e 'set winName to "sin ventana"' \
-e 'end try' \
-e 'return frontApp & " — " & winName' \
-e 'end tell' \
-e 'end try'`;

  function attempt(useLocal) {
    return new Promise((resolve) => {
      const sshArgs = buildSshArgs(machine, useLocal);
      sshArgs.push(remoteScript);
      execFile("ssh", sshArgs, { timeout: 10_000 }, (error, stdout) => {
        if (error) resolve(null);
        else resolve(parseOutput(stdout));
      });
    });
  }

  if (deriveLocalHostname(machine)) {
    const text = await attempt(true);
    if (text) return text;
  }
  return attempt(false);
}

export async function refreshAllSnapshots() {
  const data = await readMachines();
  const sshEnabled = data.machines.filter((m) => m.ssh?.enabled);

  await Promise.allSettled(
    sshEnabled.map(async (machine) => {
      const text = await captureOneSnapshot(machine);
      if (text) {
        machineSnapshots.set(machine.id, {
          text,
          updatedAt: new Date().toISOString()
        });
      }
    })
  );
}

// Start periodic refresh
refreshAllSnapshots();
setInterval(refreshAllSnapshots, 60_000);

export function resolveMachineName(machines, input) {
  const q = input.toLowerCase().replace(/[\s-_]+/g, "");
  return machines.find((m) => {
    const id = m.id.toLowerCase().replace(/[\s-_]+/g, "");
    const name = m.name.toLowerCase().replace(/[\s-_]+/g, "");
    return id.includes(q) || name.includes(q) || id.replace("admira", "").includes(q);
  }) || null;
}
