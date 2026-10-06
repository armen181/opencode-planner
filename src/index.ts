/**
 * @armen181/opencode-planner — OpenCode V2 server plugin.
 *
 * The planner is OPT-IN: normal sessions (Build/Plan/…) are completely
 * untouched. The plugin installs the agent definitions, keeps the configured
 * subagent models applied, registers the `/plan` command, and only for
 * requests running the `planner` agent injects the cycle/parallelism/vision
 * instructions through the `session.context` hook. Configuration dialogs live
 * in the TUI plugin (`src/tui.ts`) and write `.opencode/planner-plugin.json`,
 * which this plugin watches.
 *
 * MCP servers are managed by OpenCode itself (the native `/mcps` command and
 * the `mcp` section of opencode.json) — this plugin never touches them.
 */
import type { Plugin as PluginApi } from "@opencode/plugin"
import { statSync } from "node:fs"
import {
  CONFIGURABLE_AGENTS,
  NATIVE_VISION_INSTRUCTION,
  PROXY_VISION_INSTRUCTION,
  VISION_INSTRUCTION,
  agentFileExists,
  ensureAgentFiles,
  findModel,
  migrateLegacyConfig,
  modelCanSeeImages,
  modelRefFromSpec,
  plannerConfigPath,
  readPlannerConfig,
  screenshotDirFor,
  type PlannerConfig,
} from "./core"

const SCREENSHOT_FRESH_MS = 5 * 60 * 1000
const MAX_NOTES_PER_TOOLRUN = 3

type PlannerContext = PluginApi.Context

/** `Plugin.define` is an identity helper; avoiding the runtime import keeps
 *  the plugin loadable from any discovered directory with zero dependencies. */
function define<P>(plugin: P): P {
  return plugin
}

/** Absolute .png paths mentioned in a tool run's output. Quoted paths are
 *  matched first so paths containing spaces work too. */
function pngPathsFrom(value: unknown): string[] {
  const s = typeof value === "string" ? value : JSON.stringify(value ?? "")
  const out: string[] = []
  const add = (p: string) => {
    if (p.startsWith("/") && p.toLowerCase().endsWith(".png") && !out.includes(p)) out.push(p)
  }
  for (const m of s.match(/"([^"\n]+\.png)"|'([^'\n]+\.png)'/gi) ?? []) {
    add(m.slice(1, -1).trim())
  }
  for (const m of s.match(/\/[^\s"']+\.png/gi) ?? []) add(m.trim())
  return out
}

function isFreshPng(path: string): boolean {
  try {
    return Date.now() - statSync(path).mtimeMs <= SCREENSHOT_FRESH_MS
  } catch {
    return false
  }
}

function logWarn(message: string): void {
  try {
    console.error(`[planner] ${message}`)
  } catch {
    // logging must never break the plugin
  }
}

export default define({
  id: "planner",
  async setup(ctx: PlannerContext): Promise<() => void> {
    const repo: string = ctx.location?.directory ?? process.cwd()
    // Adopt a legacy `.opencode/planner.json` before reading, so an upgrade
    // never looks like a fresh (unconfigured) install.
    try {
      migrateLegacyConfig(repo)
    } catch {
      // best-effort migration
    }

    // The agent definitions are what make the planner selectable (Tab / /plan).
    // Best-effort: a read-only project must not fail the whole plugin.
    try {
      const warnings = ensureAgentFiles(repo)
      if (warnings.length) logWarn(`agent templates missing: ${warnings.join("; ")}`)
    } catch (e) {
      logWarn(`could not install agent definitions: ${String(e)}`)
    }

    const cfgPath = plannerConfigPath(repo)
    let cfg: PlannerConfig = readPlannerConfig(repo)
    let lastMtime = mtimeOf(cfgPath)

    // -----------------------------------------------------------------------
    // Agents: per-agent models + effort variants for the SUBAGENTS
    // (brainstormer/worker/tester, optional vision). The planner itself is
    // never pinned and NO default agent is set — normal sessions stay normal.
    // -----------------------------------------------------------------------
    await ctx.agent.transform((editor) => {
      for (const name of CONFIGURABLE_AGENTS) {
        // Disk check (not editor.get): definitions written at runtime can be
        // picked up after this transform is registered, and update() patches
        // still apply once the agent is discovered.
        if (!agentFileExists(repo, name)) continue
        const ref = modelRefFromSpec(cfg.models[name])
        if (!ref) continue
        const variant = cfg.variants?.[name]
        if (variant) ref.variant = variant
        editor.update(name, (agent) => {
          agent.model = ref as unknown as typeof agent.model
        })
      }
      if (cfg.vision.enabled && (cfg.vision.mode ?? "agent") === "agent" && agentFileExists(repo, "vision")) {
        const ref = modelRefFromSpec(cfg.vision.model)
        if (ref) {
          if (cfg.vision.variant) ref.variant = cfg.vision.variant
          editor.update("vision", (agent) => {
            agent.model = ref as unknown as typeof agent.model
          })
        }
      }
    })

    /** Re-read the config file and re-apply the agent transforms (live apply
     *  for TUI dialog edits, which happen in the CLI process). */
    async function applyConfigChange(): Promise<void> {
      cfg = readPlannerConfig(repo)
      try {
        await ctx.agent.reload()
      } catch {
        // agents reload is best-effort; the next config change retries
      }
    }

    // -----------------------------------------------------------------------
    // Commands: /plan only. Configuration lives in the TUI dialogs.
    // -----------------------------------------------------------------------
    await ctx.command.transform((editor) => {
      editor.add({
        name: "plan",
        description: "Plan a request: switch this session to Planner and delegate to worker/tester subagents",
        execute: async ({ sessionID, prompt, delivery }) => {
          const request = (typeof prompt?.text === "string" ? prompt.text : "").replace(/^\s*\/plan\b\s*/i, "").trim()
          await ctx.session.switchAgent({ sessionID, agent: "planner" as never })
          await ctx.session.prompt({ ...prompt, sessionID, text: request || prompt.text, delivery })
        },
      })
    })

    // -----------------------------------------------------------------------
    // Context hook: cycle limit + parallelism + vision instructions, ONLY for
    // requests running the planner agent. Normal sessions get nothing.
    // -----------------------------------------------------------------------
    let modelListCache: { at: number; value: unknown } | undefined
    async function models(): Promise<unknown> {
      const now = Date.now()
      if (modelListCache && now - modelListCache.at < 30_000) return modelListCache.value
      try {
        const value = await ctx.model.list()
        modelListCache = { at: now, value }
        return value
      } catch {
        return undefined
      }
    }

    async function visionInstruction(model: { providerID?: string; id?: string } | undefined): Promise<string | undefined> {
      if (!cfg.vision.enabled) return undefined
      if (model?.providerID && model?.id) {
        const active = `${model.providerID}/${model.id}`
        if (active === cfg.vision.model.split("#")[0]) return undefined
      }
      const info =
        model?.providerID && model?.id ? findModel(await models(), model.providerID, model.id) : undefined
      if (info && modelCanSeeImages(info)) return undefined
      const mode = cfg.vision.mode ?? "agent"
      if (mode === "proxy") return PROXY_VISION_INSTRUCTION
      if (mode === "native") return NATIVE_VISION_INSTRUCTION
      return VISION_INSTRUCTION
    }

    await ctx.session.hook("context", async (event) => {
      if (event.agent !== "planner") return
      // ONE system item per hook: strict chat templates (Qwen on vLLM etc.)
      // accept only a single system message, so never add more than one.
      const blocks: string[] = [
        `# Worker/tester cycle limit\n\n` +
          `The configured maximum number of worker→tester fix cycles for this project is ` +
          `${cfg.maxCycles} (maxCycles). After ${cfg.maxCycles} failed cycles, STOP re-delegating ` +
          `and report to the user what is still blocking the request.`,
        `# Step control — check each result yourself\n\n` +
          `You control the flow: after the brainstormer returns, READ its spec file yourself and verify ` +
          `it covers the request before delegating; after the worker returns, inspect the changed files ` +
          `yourself before delegating to the tester. The brainstormer never asks questions — never wait ` +
          `for input on its behalf.`,
      ]
      const maxParallel = cfg.maxParallelSubagents ?? 1
      blocks.push(
        maxParallel <= 1
          ? `# Subagent execution — strictly serial\n\n` +
              `Run subagent tasks ONE AT A TIME: never start a task (worker, tester, vision) while ` +
              `another task is still running, and never issue several subagent calls in one message. ` +
              `Wait for each result before starting the next (maxParallelSubagents=1). This machine ` +
              `serves a single model request at a time, so parallel tasks only slow everything down.`
          : `# Subagent execution — at most ${maxParallel} in parallel\n\n` +
              `Run at most ${maxParallel} subagent tasks at the same time (maxParallelSubagents=${maxParallel}). ` +
              `If you have more independent tasks, queue them and start the next only when one has returned.`,
      )
      const instruction = await visionInstruction(event.model as { providerID?: string; id?: string } | undefined)
      event.system.push({
        type: "text",
        text: instruction ? `${blocks.join("\n\n")}\n\n${instruction}` : blocks.join("\n\n"),
      })
    })

    // -----------------------------------------------------------------------
    // Shell hook: where agents save screenshots for this project.
    // -----------------------------------------------------------------------
    const shots = screenshotDirFor(repo)
    try {
      await ctx.shell.hook("create.before", (event) => {
        event.env.OPENCODE_SCREENSHOT_DIR = shots
      })
    } catch {
      // shell hook unavailable (older build) — screenshots stay best-effort
    }

    // -----------------------------------------------------------------------
    // Screenshot reporting (opt-in): a fresh .png from a write/edit/shell run
    // is reported to the ROOT session when `screenshotNotes` is enabled.
    // -----------------------------------------------------------------------
    async function rootSession(sessionID: string): Promise<string | undefined> {
      let current = sessionID
      const seen = new Set<string>()
      while (current && !seen.has(current)) {
        seen.add(current)
        try {
          const res = (await ctx.session.get({ sessionID: current } as never)) as unknown as {
            data?: { parentID?: string }
            parentID?: string
          }
          const info = res?.data ?? res
          if (!info?.parentID) return current
          current = info.parentID
        } catch {
          return current
        }
      }
      return current
    }

    const noted = new Map<string, number>()
    try {
      await ctx.tool.hook("execute.after", async (event) => {
        try {
          const e = event as unknown as {
            status?: string
            tool?: string
            input?: { filePath?: string; file_path?: string }
            result?: unknown
            sessionID?: string
          }
          if (e?.status && e.status !== "completed") return
          if (e?.tool !== "shell" && e?.tool !== "bash" && e?.tool !== "write" && e?.tool !== "edit" && e?.tool !== "patch") return
          const candidates: string[] = []
          const inputPath = e?.input?.filePath ?? e?.input?.file_path
          if (typeof inputPath === "string" && inputPath.endsWith(".png")) candidates.push(inputPath)
          for (const p of pngPathsFrom(e?.result)) {
            // The shared screenshot dir is used by EVERY session in this
            // project; a file merely mentioned in tool output may belong to
            // another session. Explicit write/edit targets above are trusted.
            if (p.startsWith(shots + "/")) continue
            if (!candidates.includes(p)) candidates.push(p)
          }
          let count = 0
          for (const p of candidates) {
            if (count >= MAX_NOTES_PER_TOOLRUN) break
            if (!isFreshPng(p)) continue
            const mtime = statSync(p).mtimeMs
            if (noted.get(p) === mtime) continue
            noted.set(p, mtime)
            if (cfg.screenshotNotes !== true) continue
            const target = e?.sessionID ? await rootSession(e.sessionID) : undefined
            if (target) await ctx.session.synthetic({ sessionID: target, text: `Screenshot captured: ${p}` })
            count++
          }
        } catch {
          // best-effort
        }
      })
    } catch {
      // tool hook unavailable — skip
    }

    // -----------------------------------------------------------------------
    // Live config application: poll the config file mtime — the TUI dialogs
    // write it from the CLI process, so the server re-reads it and reloads the
    // agent registry without a restart.
    // -----------------------------------------------------------------------
    const timer = setInterval(() => {
      const m = mtimeOf(cfgPath)
      if (m !== lastMtime) {
        lastMtime = m
        void applyConfigChange()
      }
    }, 1500)

    return () => {
      clearInterval(timer)
    }
  },
})

function mtimeOf(path: string): number {
  try {
    return statSync(path).mtimeMs
  } catch {
    return 0
  }
}
