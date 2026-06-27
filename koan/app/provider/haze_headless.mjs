#!/usr/bin/env node
/**
 * Haze headless runner for Kōan.
 *
 * Haze (https://github.com/DenizOkcu/haze) is an interactive Ink/React TUI and
 * has no native non-interactive / print mode. Kōan needs a scripted, headless
 * agent invocation that reads a prompt, runs the tool-loop, and returns the
 * final assistant text — exactly what Haze's `runAgentTurn` core does.
 *
 * This bridge imports Haze's installed package internals and drives the agent
 * core directly, emitting Koan-style JSONL progress events on stdout and
 * writing the final assistant text to a result file. It is invoked by the
 * `HazeProvider` Python class (provider/haze.py).
 *
 * Usage:
 *   node haze_headless.mjs --haze-root <pkg-dir> --prompt-file <path> \
 *       [--model <selector>] [--debug] [--last-message <path>] [--cwd <dir>]
 *
 * Contract:
 *   - Reads the prompt from --prompt-file (UTF-8). This keeps long prompts out
 *     of argv (no ps leak / ARG_MAX) and matches Haze's own no-positional-prompt
 *     CLI design.
 *   - Prints one JSON object per line to stdout (Koan stream-json compatible).
 *   - Writes the final assistant text to --last-message file when provided.
 *   - Exits 0 on success, 1 on failure (error message on stderr).
 *   - --model accepts a "providerName:modelId" selector or a bare model id and
 *     applies it via a one-shot settings overlay (never persists to disk).
 */

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

// ---------------------------------------------------------------------------
// Argument parsing
// ---------------------------------------------------------------------------
const args = process.argv.slice(2);
function getArg(name) {
  const idx = args.indexOf(name);
  return idx >= 0 && idx + 1 < args.length ? args[idx + 1] : null;
}
function hasFlag(name) {
  return args.includes(name);
}

const debug = hasFlag("--debug");
const hazeRoot = getArg("--haze-root");
const promptFile = getArg("--prompt-file");
const promptInline = getArg("--prompt");
const modelSelector = getArg("--model") || "";
const lastMessagePath = getArg("--last-message") || "";
const cwdArg = getArg("--cwd") || process.cwd();

if (!hazeRoot) {
  console.error("haze_headless: --haze-root <package-dir> is required");
  process.exit(2);
}
if (!promptFile && promptInline === null) {
  console.error("haze_headless: --prompt-file <path> or --prompt <text> is required");
  process.exit(2);
}

const log = (...parts) => {
  if (debug) console.error("[haze-bridge]", ...parts);
};

// ---------------------------------------------------------------------------
// Resolve Haze package internals from the installed location.
// ---------------------------------------------------------------------------
// We resolve relative to the package root so the bridge works regardless of
// whether @denizokcu/haze is in a project node_modules or a global prefix.
const distDir = join(hazeRoot, "dist");
const requireFromPkg = createRequire(join(hazeRoot, "package.json"));

function emit(event) {
  try {
    process.stdout.write(JSON.stringify({ ...event, at: new Date().toISOString() }) + "\n");
  } catch {
    // Best-effort progress; never crash the loop on a serialization error.
  }
}

// ---------------------------------------------------------------------------
// Optional model selection overlay.
//
// Haze reads its active model from ~/.haze/settings.json. It exposes no
// --model CLI flag. To honor Koan's per-mission model config we read the
// settings, resolve the selector the same way Haze does, and patch the
// in-memory settings before the turn. We never write the patched settings
// back to disk — this is a per-invocation override.
// ---------------------------------------------------------------------------
async function resolveSettingsWithModel() {
  const { readSettings } = await import(`file://${join(distDir, "config", "settings.js")}`);
  const settings = await readSettings();
  if (!modelSelector) return settings;

  // Mirror Haze's resolveModelSelector so a "providerName:modelId" or bare
  // model id is accepted identically to /model inside the TUI.
  try {
    const providers = await import(`file://${join(distDir, "config", "providers.js")}`);
    const result = providers.resolveModelSelector(settings, modelSelector);
    if (result.status === "found") {
      return { ...settings, provider: result.provider.name, model: result.model };
    }
    log(`model selector '${modelSelector}' not resolved (status=${result.status}); using default`);
  } catch (err) {
    log(`model override skipped: ${err?.message || err}`);
  }
  return settings;
}

async function applyModelOverride() {
  if (!modelSelector) return;
  const patched = await resolveSettingsWithModel();
  // Monkey-patch readSettings for this process only so assembleRequestContext
  // and modelWithConfig pick up the override. This is safe because the bridge
  // is a short-lived single-turn process.
  const settingsModule = await import(`file://${join(distDir, "config", "settings.js")}`);
  settingsModule.readSettings = async () => patched;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function main() {
  await applyModelOverride();

  let prompt;
  if (promptFile) {
    prompt = readFileSync(promptFile, "utf8");
  } else {
    prompt = promptInline ?? "";
  }
  log(`prompt length: ${prompt.length} chars`);

  // Dynamically import Haze's agent core so a broken/missing install produces
  // a clear error rather than a stack trace.
  let runAgentTurn;
  try {
    const mod = await import(`file://${join(distDir, "cli", "commands", "streaming.js")}`);
    runAgentTurn = mod.runAgentTurn;
  } catch (err) {
    console.error(`haze_headless: failed to import Haze agent core from ${distDir}: ${err?.message || err}`);
    process.exit(1);
  }
  if (typeof runAgentTurn !== "function") {
    console.error("haze_headless: Haze package did not export runAgentTurn");
    process.exit(1);
  }

  emit({ type: "system", subtype: "init" });

  let conversation = [];
  let finalText = "";
  let lastError = "";
  let usage = null;

  const noop = () => {};
  const callbacks = {
    setBusy: noop,
    setBusyLabel: noop,
    setAbortController: noop,
    addMessage: (m) => {
      if (m?.role === "assistant" && typeof m.text === "string") {
        finalText = m.text;
        emit({ type: "assistant", text: m.text });
      }
    },
    updateMessage: noop,
    getConversation: () => conversation,
    setConversation: (msgs) => { conversation = msgs; },
    getLastAssistantText: () => finalText,
    setLastAssistantText: (t) => { finalText = t; },
    compactConversation: () => false,
    onEvent: (e) => {
      if (!e) return;
      switch (e.type) {
        case "tool_start":
          emit({ type: "tool_use", name: e.name, id: e.id });
          log("tool:", e.name);
          break;
        case "tool_end":
          emit({ type: "tool_result", id: e.id, name: e.name, success: !!e.success });
          break;
        case "retry":
          emit({ type: "retry", attempt: e.attempt, error: e.error });
          break;
        default:
          break;
      }
    },
    debugLog: (s) => { if (debug) console.error("[haze-bridge]", String(s)); },
    recordTokenUsage: (u) => {
      if (u && typeof u === "object") usage = { ...usage, ...u };
    },
    log: noop,
    onTasksChanged: noop,
    setWorkState: noop,
    setGoalStatus: noop,
  };

  // A session-like object so Haze scopes file access and sessions to cwd.
  // Passing undefined session makes Haze use process.cwd() defaults, which is
  // what we want — the Python provider sets cwd via subprocess.
  let session = undefined;
  // Honor --no-session semantics: Haze saves durable sessions by default; we
  // disable persistence for headless runs by leaving session undefined (Haze
  // only persists when a session object is passed through).

  try {
    await runAgentTurn(
      prompt,
      undefined,
      [],
      callbacks,
      0,
      false,
      false,
      session,
    );

    // Emit a Koan-compatible result event.
    emit({
      type: "result",
      subtype: "success",
      result: finalText,
      ...(usage ? { usage } : {}),
    });

    if (lastMessagePath) {
      writeFileSync(lastMessagePath, finalText, "utf8");
    }
    process.exit(0);
  } catch (err) {
    lastError = err?.message || String(err);
    console.error(`haze_headless: agent turn failed: ${lastError}`);
    emit({ type: "result", subtype: "error", error: lastError });
    process.exit(1);
  }
}

main().catch((err) => {
  console.error(`haze_headless: uncaught error: ${err?.message || err}`);
  process.exit(1);
});
