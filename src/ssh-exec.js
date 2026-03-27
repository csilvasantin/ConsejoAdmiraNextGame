import { execFile } from "node:child_process";
import { resolve } from "node:path";
import { mkdir } from "node:fs/promises";
import { readMachines } from "./store.js";

const TIMEOUT_MS = 15_000;
const CAPTURE_DELAY_MS = 4000;
const SCREENSHOTS_DIR = resolve(import.meta.dirname, "../data/screenshots");

// Ensure screenshots dir exists
mkdir(SCREENSHOTS_DIR, { recursive: true }).catch(() => {});

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
  const osascript = [
    `tell application "${appName}" to activate`,
    `tell application "System Events" to keystroke "${safe}"`,
    'tell application "System Events" to keystroke return'
  ];
  const remoteCmd = osascript.map((line) => `-e '${line}'`).join(" ");

  function tryExec(useLocal) {
    const sshArgs = buildSshArgs(machine, useLocal);
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

  let result = await tryExec(false);
  let usedLocal = false;
  if (!result.ok && deriveLocalHostname(machine)) {
    result = await tryExec(true);
    usedLocal = true;
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

export function resolveMachineName(machines, input) {
  const q = input.toLowerCase().replace(/[\s-_]+/g, "");
  return machines.find((m) => {
    const id = m.id.toLowerCase().replace(/[\s-_]+/g, "");
    const name = m.name.toLowerCase().replace(/[\s-_]+/g, "");
    return id.includes(q) || name.includes(q) || id.replace("admira", "").includes(q);
  }) || null;
}
