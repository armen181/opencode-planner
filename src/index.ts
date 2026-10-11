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
import { appendFileSync, statSync } from "node:fs"
import {
  CONFIGURABLE_AGENTS,
  NATIVE_VISION_INSTRUCTION,
  PROXY_VISION_INSTRUCTION,
  VISION_INSTRUCTION,
  agentFileExists,
  ensureAgentFiles,
  ensureDefaultsFile,
  ensureSpecsDir,
  findModel,
  migrateLegacyConfig,
  modelCanSeeImages,
  modelRefFromSpec,
  plannerConfigPath,
  readPlannerConfig,
  screenshotDirFor,
  snapshotProjectConfig,
  type PlannerConfig,
} from "./core"
import { Semaphore } from "./semaphore"

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

/** Opt-in debug logging (PLANNER_DEBUG=1). Used by the e2e suites to observe
 *  the subagent concurrency gate without a debugger. When PLANNER_DEBUG_FILE
 *  is set, every line is also appended there synchronously — the server's
 *  stdout is buffered, so the file is what live tests read. */
function debugLog(message: string): void {
  if (process.env.PLANNER_DEBUG !== "1") return
  const line = `[planner] ${message}`
  try {
    console.error(line)
  } catch {
    // logging must never break the plugin
  }
  const file = process.env.PLANNER_DEBUG_FILE
  if (file) {
    try {
      appendFileSync(file, `${line} [t=${new Date().toISOString()}]\n`)
    } catch {
      // the debug sink is best-effort
    }
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

    // Global defaults: create the file on first load so it is editable even
    // before any project runs the planner.
    try {
      ensureDefaultsFile()
    } catch {
      // best-effort
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
    // Subagent concurrency cap — ENFORCED, not advisory. The built-in
    // `subagent` tool is wrapped with a semaphore: extra calls wait for a free
    // slot instead of running in parallel. The permission hook below is the
    // fallback for builds where the tool cannot be wrapped. Some OpenCode
    // builds name the delegation tool `task` (Claude-Code style) — the cap
    // must apply to whichever of the two exists, never to both (an alias
    // delegating into the wrapped canonical would double-acquire and stall).
    // -----------------------------------------------------------------------
    const subagentGate = new Semaphore(() => cfg.maxParallelSubagents ?? 1)
    let subagentSeq = 0
    let subagentWrapped = false
    /** The delegation tool's name on THIS build — used by every injected
     *  instruction so the model is never told to call a tool that does not
     *  exist (a top source of "tool not available" failures). */
    let delegationToolName = "subagent"
    try {
      await ctx.tool.transform((editor) => {
        const wrapName = editor.get("subagent") ? "subagent" : editor.get("task") ? "task" : ""
        if (!wrapName) return
        delegationToolName = wrapName
        editor.update(wrapName, (tool) => {
          const current = tool.execute as unknown
          if (typeof current !== "function") return
          // A transform replay can hand us back our own wrapper — never wrap twice.
          if ((current as { __plannerGate?: boolean }).__plannerGate) {
            subagentWrapped = true
            return
          }
          const original = current as (input: unknown, context: unknown) => Promise<unknown>
          const gated = async (input: unknown, context: { agent?: string } | undefined) => {
            const caller = context?.agent
            if (caller && caller !== "planner") return original.call(tool, input, context)
            const seq = ++subagentSeq
            const queuedAt = Date.now()
            await subagentGate.acquire()
            debugLog(
              `subagent#${seq} acquire inFlight=${subagentGate.inFlight} waiting=${subagentGate.waiting} waitedMs=${Date.now() - queuedAt} caller=${caller ?? "?"}`,
            )
            try {
              return await original.call(tool, input, context)
            } finally {
              subagentGate.release()
              debugLog(`subagent#${seq} release inFlight=${subagentGate.inFlight} waiting=${subagentGate.waiting}`)
            }
          }
          ;(gated as unknown as { __plannerGate?: boolean }).__plannerGate = true
          tool.execute = gated as typeof tool.execute
          subagentWrapped = true
          debugLog("subagent tool wrapped (concurrency cap enforced)")
        })
      })
    } catch (e) {
      logWarn(`could not wrap the subagent tool (permission fallback active): ${String(e)}`)
    }

    // Fallback cap: when the wrapper is not installed on this build, deny a
    // subagent call that would exceed the limit (tracked by tool-call id).
    const fallbackAdmitted = new Map<string, number>()
    try {
      await ctx.permission.hook("evaluate", (event) => {
        // The planner coordinates and verifies; it never writes files itself.
        // Enforced server-side so the rule holds no matter how the agent
        // definition was loaded (agent-file formats differ across builds).
        // `execute` (Code Mode) is denied too — it is a shell/write path that
        // let the planner build the request itself, bypassing delegation.
        if (
          event.agent === "planner" &&
          (event.action === "edit" ||
            event.action === "write" ||
            event.action === "patch" ||
            event.action === "shell" ||
            event.action === "bash" ||
            event.action === "execute")
        ) {
          event.effect = "deny"
          event.message =
            "The planner must not run commands or write files — delegate this to the subagents " +
            "(the brainstormer writes the spec, the worker changes code)."
          return
        }
        if (subagentWrapped) return
        if (event.action !== "subagent" || event.effect !== "allow") return
        if (event.agent && event.agent !== "planner") return
        const now = Date.now()
        for (const [id, at] of fallbackAdmitted) if (now - at > 30 * 60_000) fallbackAdmitted.delete(id)
        const limit = cfg.maxParallelSubagents ?? 1
        if (fallbackAdmitted.size >= limit) {
          event.effect = "deny"
          event.message =
            `maxParallelSubagents=${limit}: a subagent task is already running. ` +
            `Wait for it to finish, then call the ${delegationToolName} tool again.`
          debugLog(`subagent call denied by fallback cap (admitted=${fallbackAdmitted.size})`)
          return
        }
        const id = event.source?.id
        if (id) fallbackAdmitted.set(id, now)
      })
    } catch {
      // permission hook unavailable — the wrapper remains the only mechanism
    }

    // -----------------------------------------------------------------------
    // Delegation watchdog: the planner must delegate — brainstormer first,
    // worker + tester for changes. A planner turn that ends without any
    // subagent call (or with only a READY brainstormer spec and no
    // worker/tester) is resumed with a bounded number of nudges.
    // -----------------------------------------------------------------------
    const NUDGE_PREFIX = "[planner-delegation-check]"
    const MAX_NUDGES_PER_TURN = 2
    const NUDGE_TEXT =
      `${NUDGE_PREFIX} Your previous turn ended without the required delegation. ` +
      `The planner workflow is MANDATORY: delegate to the \`brainstormer\` subagent for EVERY request ` +
      `FIRST (only it may classify a request as trivial), and for any code change follow with the ` +
      `\`worker\` and then the \`tester\` subagent. Do not answer or finish the request yourself — ` +
      `never write the spec or change code yourself. If the brainstormer replied NEEDS_INPUT or FAILED, ` +
      `relay its questions to the user with the \`question\` tool (pass its answer options) or re-delegate ` +
      `to it with concrete feedback — do not do its work yourself. Continue now by delegating with the ` +
      `\`${delegationToolName}\` tool.`

    type TurnState = {
      calls: number
      brainstormer: "none" | "ready" | "trivial" | "needs_input" | "unknown"
      workerOrTester: number
      nudges: number
      /** The planner wrote/edited files itself this turn (it must not). */
      wroteFiles: boolean
      /** The planner asked the user a question this turn (legitimately waits). */
      askedUser: boolean
      /** Identical-call repetition counts (small models spiral on failures). */
      loopCounts: Record<string, number>
      loopNudges: number
      active: boolean
      last: number
    }
    const turns = new Map<string, TurnState>()
    const plannerSessions = new Set<string>()

    function turnOf(sessionID: string): TurnState {
      let t = turns.get(sessionID)
      if (!t) {
        t = {
          calls: 0,
          brainstormer: "none",
          workerOrTester: 0,
          nudges: 0,
          wroteFiles: false,
          askedUser: false,
          loopCounts: {},
          loopNudges: 0,
          active: false,
          last: Date.now(),
        }
        turns.set(sessionID, t)
        if (turns.size > 200) {
          let oldestKey = ""
          let oldest = Infinity
          for (const [k, v] of turns) {
            if (v.last < oldest) {
              oldest = v.last
              oldestKey = k
            }
          }
          if (oldestKey) turns.delete(oldestKey)
        }
      }
      t.last = Date.now()
      return t
    }

    function subagentTarget(input: unknown): string {
      const i = input as { agent?: unknown; subagent?: unknown; name?: unknown; subagent_type?: unknown } | undefined
      const target = i?.agent ?? i?.subagent ?? i?.subagent_type ?? i?.name
      return typeof target === "string" ? target : ""
    }

    function resultTextOf(result: unknown): string {
      const parts: string[] = []
      const push = (v: unknown): void => {
        if (typeof v === "string") parts.push(v)
        else if (Array.isArray(v)) for (const x of v) push((x as { text?: unknown })?.text ?? x)
        else if (v && typeof v === "object") push((v as { text?: unknown }).text)
      }
      const r = result as { content?: unknown; output?: unknown } | undefined
      push(r?.content)
      push(r?.output)
      return parts.join("\n")
    }

    function recordSubagentAttempt(sessionID: string, input: unknown): void {
      noteActivity(sessionID)
      debugLog(`watchdog: subagent attempt target=${subagentTarget(input) || "?"} session=${sessionID}`)
    }

    function recordSubagentCall(sessionID: string, input: unknown): void {
      const t = turnOf(sessionID)
      t.calls++
      t.active = true
      const target = subagentTarget(input)
      if (target === "worker" || target === "tester") t.workerOrTester++
      noteActivity(sessionID)
      debugLog(`watchdog: subagent call #${t.calls} target=${target || "?"} session=${sessionID}`)
    }

    function recordSubagentResult(sessionID: string, input: unknown, result: unknown): void {
      const t = turnOf(sessionID)
      noteActivity(sessionID)
      if (subagentTarget(input) !== "brainstormer") return
      const text = resultTextOf(result)
      t.brainstormer = /STATUS:\s*TRIVIAL/i.test(text)
        ? "trivial"
        : /STATUS:\s*READY/i.test(text)
          ? "ready"
          : /STATUS:\s*NEEDS_INPUT/i.test(text)
            ? "needs_input"
            : "unknown"
      debugLog(`watchdog: brainstormer result -> ${t.brainstormer} (session=${sessionID})`)
    }

    // `session.execution.*` events mark the end of a model STEP, not of the
    // turn (a step's tool calls run right after it), so a short settle window
    // is used: any further activity cancels the pending turn-end check.
    const TURN_SETTLE_MS = 4000
    const settleTimers = new Map<string, ReturnType<typeof setTimeout>>()

    function noteActivity(sessionID: string): void {
      const timer = settleTimers.get(sessionID)
      if (timer) {
        clearTimeout(timer)
        settleTimers.delete(sessionID)
      }
    }

    function scheduleTurnEndCheck(sessionID: string): void {
      noteActivity(sessionID)
      const timer = setTimeout(() => {
        settleTimers.delete(sessionID)
        void handleTurnEnd(sessionID)
      }, TURN_SETTLE_MS)
      settleTimers.set(sessionID, timer)
    }

    async function nudge(sessionID: string, reason: string): Promise<void> {
      const t = turnOf(sessionID)
      t.nudges++
      t.active = false
      noteActivity(sessionID)
      debugLog(`watchdog: nudge ${t.nudges}/${MAX_NUDGES_PER_TURN} (${reason}) session=${sessionID}`)
      const extra =
        reason === "brainstormer asked questions that were not relayed"
          ? ` The brainstormer replied NEEDS_INPUT: relay its questions to the user with the \`question\` tool ` +
            `(pass its answer options) and WAIT for the answers — do not re-delegate yet and do not answer for the user.`
          : ""
      try {
        await ctx.session.synthetic({
          sessionID,
          text: NUDGE_TEXT + extra,
          description: "Planner delegation enforcement",
          metadata: { plannerNudge: true },
          delivery: "queue",
          resume: true,
        } as never)
      } catch (e) {
        debugLog(`watchdog: nudge delivery failed: ${String(e)}`)
      }
    }

    async function handleTurnEnd(sessionID: string): Promise<void> {
      const t = turns.get(sessionID)
      if (!t || !t.active) return
      if (cfg.enforceDelegation === false) {
        t.active = false
        return
      }
      if (!agentFileExists(repo, "brainstormer") || !agentFileExists(repo, "worker")) {
        t.active = false
        debugLog(`watchdog: subagent definitions missing — no nudge (session=${sessionID})`)
        return
      }
      if (t.nudges >= MAX_NUDGES_PER_TURN) {
        t.active = false
        return
      }
      // Waiting for user answers is a legitimate way to end a turn (the
      // planner relayed the brainstormer's questions) — never nudge that.
      if (t.askedUser) {
        resetTurn(t)
        return
      }
      const reason =
        t.calls === 0 && !t.wroteFiles
          ? "no subagent call"
          : t.brainstormer === "ready" && t.workerOrTester === 0
            ? "spec READY but no worker/tester"
            : t.brainstormer === "needs_input" && t.workerOrTester === 0
              ? "brainstormer asked questions that were not relayed"
              : t.brainstormer === "unknown" && t.workerOrTester === 0
                ? "brainstormer did not return READY or TRIVIAL"
                : t.wroteFiles && t.workerOrTester === 0
                  ? "the planner wrote files itself"
                  : ""
      if (!reason) {
        resetTurn(t)
        return
      }
      await nudge(sessionID, reason)
    }

    /** A clean turn end: clear all per-turn counters. */
    function resetTurn(t: TurnState): void {
      t.calls = 0
      t.brainstormer = "none"
      t.workerOrTester = 0
      t.nudges = 0
      t.wroteFiles = false
      t.askedUser = false
      t.loopCounts = {}
      t.loopNudges = 0
      t.active = false
    }

    // A new user prompt in a known planner session starts a fresh turn.
    try {
      await ctx.session.hook("prompt", (event) => {
        const prompt = (event as { prompt?: { text?: unknown } }).prompt
        const text = typeof prompt?.text === "string" ? prompt.text : ""
        if (text.startsWith(NUDGE_PREFIX)) return
        const sid = String((event as { sessionID?: unknown }).sessionID ?? "")
        if (!sid || !plannerSessions.has(sid)) return
        noteActivity(sid)
        const t = turnOf(sid)
        resetTurn(t)
        t.active = true
        debugLog(`watchdog: new user turn (session=${sid})`)
      })
    } catch {
      // prompt hook unavailable — turn counters reset on the next clean turn
    }

    // Turn-end detection. On OpenCode 2.0.x the plugin event stream delivers
    // `session.execution.*` (one per turn); `session.idle`/`session.status`
    // are not delivered to plugins, so they are only a secondary signal.
    const watchdogAbort = new AbortController()
    try {
      void (async () => {
        debugLog("watchdog: subscribing to session events")
        try {
          for await (const raw of ctx.event.subscribe({ signal: watchdogAbort.signal } as never)) {
            const e = raw as { type?: string; data?: { sessionID?: string; status?: { type?: string } } }
            const sid = e?.data?.sessionID
            const turnEnded =
              e?.type === "session.execution.succeeded" ||
              e?.type === "session.execution.failed" ||
              e?.type === "session.execution.interrupted" ||
              e?.type === "session.idle" ||
              (e?.type === "session.status" && e?.data?.status?.type === "idle")
            if (e?.type?.startsWith("session.execution.") || e?.type === "session.idle" || e?.type === "session.status") {
              debugLog(`watchdog: event ${e.type} session=${sid ?? "?"} status=${e?.data?.status?.type ?? ""}`)
            }
            if (!sid || !plannerSessions.has(String(sid))) continue
            noteActivity(String(sid))
            if (!turnEnded) continue
            scheduleTurnEndCheck(String(sid))
          }
          debugLog("watchdog: event stream ended normally")
        } catch (err) {
          debugLog(`watchdog: event stream error: ${String(err)}`)
        }
      })()
    } catch (err) {
      debugLog(`watchdog: subscribe failed: ${String(err)}`)
    }

    // -----------------------------------------------------------------------
    // Agents: per-agent models + effort variants for the SUBAGENTS
    // (brainstormer/worker/tester, optional vision). The planner itself is
    // never pinned and NO default agent is set — normal sessions stay normal.
    // -----------------------------------------------------------------------
    await ctx.agent.transform((editor) => {
      for (const name of CONFIGURABLE_AGENTS) {
        // No file-system gate: an agent can come from the project, the global
        // agents dir, or the opencode config — and update() patches apply when
        // the agent is discovered later. Gating on the PROJECT file made pins
        // silently fail (the subagent then inherited the main model).
        const ref = modelRefFromSpec(cfg.models[name])
        if (!ref) continue
        const variant = cfg.variants?.[name]
        if (variant) ref.variant = variant
        editor.update(name, (agent) => {
          agent.model = ref as unknown as typeof agent.model
        })
      }
      if (cfg.vision.enabled && (cfg.vision.mode ?? "agent") === "agent") {
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
      // A raised maxParallelSubagents should unblock queued subagent calls now.
      subagentGate.wake()
      try {
        await ctx.agent.reload()
      } catch {
        // agents reload is best-effort; the next config change retries
      }
    }

    /** First planner use in this project: materialize the global defaults as
     *  the project's own config snapshot (with version + source metadata).
     *  The specs directory is NOT created here — it is created when a
     *  brainstormer delegation actually starts (execute.before hook). */
    function ensureProjectSnapshot(): void {
      try {
        if (snapshotProjectConfig(repo)) {
          cfg = readPlannerConfig(repo)
          debugLog("config: created the project snapshot from the global defaults")
        }
      } catch {
        // read-only project — the effective config stays the global defaults
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
          const raw = typeof prompt?.text === "string" ? prompt.text : ""
          const request = raw.replace(/^\s*\/plan\b\s*/i, "").trim()
          ensureProjectSnapshot()
          await ctx.session.switchAgent({ sessionID, agent: "planner" as never })
          await ctx.session.prompt({ ...prompt, sessionID, text: request || raw, delivery })
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
      if (event.agent !== "planner") {
        // The session switched to another agent — stop watching it. Without
        // this, a planner→Build switch left the watchdog nudging a Build
        // session for "not delegating".
        plannerSessions.delete(String(event.sessionID))
        return
      }
      // First planner use in this project materializes the config snapshot.
      ensureProjectSnapshot()
      // Watchdog bookkeeping: this session runs the planner, and a model
      // request means a turn is in progress (checked again when it idles).
      plannerSessions.add(String(event.sessionID))
      noteActivity(String(event.sessionID))
      turnOf(String(event.sessionID)).active = true
      // ONE system item per hook: strict chat templates (Qwen on vLLM etc.)
      // accept only a single system message, so never add more than one.
      const blocks: string[] = [
        `# Worker/tester cycle limit\n\n` +
          `The configured maximum number of worker→tester fix cycles for this project is ` +
          `${cfg.maxCycles} (maxCycles). After ${cfg.maxCycles} failed cycles, STOP re-delegating ` +
          `and report to the user what is still blocking the request.`,
        `# Delegation is mandatory — you are not a builder\n\n` +
          `Every request MUST go through the subagents: the brainstormer runs FIRST for EVERY request ` +
          `(only the brainstormer may classify it as trivial — you never skip this step yourself), and ` +
          `every code change runs through the worker and then the tester. The plugin watches each turn: ` +
          `a turn that ends without delegation is resumed automatically, and a READY spec that never ` +
          `reaches the worker gets resumed too. Never answer or finish a request yourself instead of ` +
          `delegating.\n\n` +
          `Keep it short — do not overthink. Reply with brief status updates, not design documents: ` +
          `check the brainstormer's requirements/spec file, delegate to the worker, check the result, ` +
          `then ask the tester to verify. Do not reason through implementation details; that is the ` +
          `worker's job.\n\n` +
          `Automatic continuation messages ("Continue working toward the active session goal" or any ` +
          `goal/loop continuation) are NOT new requests: never re-brainstorm and never implement ` +
          `yourself — continue the pipeline from where it stopped.\n\n` +
          `You control the flow: after the brainstormer returns, READ its spec file yourself and verify ` +
          `the FILE exists on disk (non-empty, numbered requirements + acceptance criteria) before ` +
          `delegating; after the worker returns, inspect the changed files yourself before delegating to ` +
          `the tester. If the brainstormer asks questions (STATUS: NEEDS_INPUT), relay them to the user ` +
          `with the \`question\` tool and re-delegate with the answers — up to 3 rounds. When relaying, ` +
          `pass the brainstormer's answer options as REAL choices (2-4 concrete alternatives per ` +
          `question, the recommended one first) — never reduce an open decision to yes/no, and never ` +
          `invent answers yourself.\n\n` +
          `Tool names are exact: \`${delegationToolName}\` (delegate), \`question\` (ask the user), ` +
          `\`read\`/\`glob\`/\`grep\` (inspect). Never invent variants like \`ask_user\`.\n\n` +
          `You have NO file-writing tools: \`write\`/\`edit\`/\`patch\` do not exist for you and calling them ` +
          `always fails. If the brainstormer's spec file is missing or wrong, do NOT try to save or fix it ` +
          `yourself — re-delegate to the brainstormer with concrete feedback. If ANY tool call fails with ` +
          `"No tool named ... is currently available", the NAME was wrong, not the action: retry once with the ` +
          `exact names above and never invent \`mcp__\`-prefixed variants.`,
      ]
      const maxParallel = cfg.maxParallelSubagents ?? 1
      blocks.push(
        maxParallel <= 1
          ? `# Subagent execution — strictly serial (plugin-enforced)\n\n` +
              `maxParallelSubagents=1 for this project and the plugin ENFORCES it: if you issue ` +
              `several subagent calls at once, only ONE starts — the others wait until it finishes ` +
              `(they are queued, not rejected). Work one task at a time anyway: wait for each ` +
              `result before starting the next. This machine serves a single model request at a ` +
              `time, so parallel tasks only slow everything down.`
          : `# Subagent execution — at most ${maxParallel} in parallel (plugin-enforced)\n\n` +
              `The plugin allows at most ${maxParallel} subagent tasks to run at the same time ` +
              `(maxParallelSubagents=${maxParallel}); extra calls wait for a free slot. If you have ` +
              `more independent tasks, queue them and start the next only when one has returned.`,
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
    /** The delegation tool name differs across OpenCode builds (`subagent` on
     *  2.0.x, `task` on Claude-Code-shaped builds); hooks must accept both. */
    const DELEGATION_TOOLS = new Set(["subagent", "task"])
    try {
      await ctx.tool.hook("execute.before", (event) => {
        const e = event as unknown as { tool?: string; sessionID?: string; input?: unknown }
        if (!e?.tool || !DELEGATION_TOOLS.has(e.tool) || !e.sessionID) return
        // The specs directory exists exactly when a brainstormer run needs it —
        // never on mere plugin load (a project that never uses the planner
        // must not grow plugin directories).
        if (subagentTarget(e.input) === "brainstormer") ensureSpecsDir(repo)
        if (!plannerSessions.has(String(e.sessionID))) return
        // Loop-breaker: the SAME call repeated is a small-model spiral (seen
        // live: an identical `execute` call fired 10+ times). Interject with a
        // bounded corrective message instead of letting it burn the turn.
        {
          const sid = String(e.sessionID)
          const t = turnOf(sid)
          const key = `${e.tool}:${JSON.stringify(e.input ?? {}).slice(0, 400)}`
          t.loopCounts[key] = (t.loopCounts[key] ?? 0) + 1
          const n = t.loopCounts[key]
          if (n >= 3 && n % 2 === 1 && t.loopNudges < 2) {
            t.loopNudges++
            debugLog(`watchdog: loop-break ${n}x ${e.tool} (session=${sid})`)
            void ctx.session
              .synthetic({
                sessionID: sid,
                text:
                  `${NUDGE_PREFIX} You have issued the SAME tool call ${n} times in a row (${e.tool}). ` +
                  `It is not working. STOP retrying it: run a DIFFERENT tool or approach, or DELEGATE the work ` +
                  `with the \`${delegationToolName}\` tool (brainstormer for the spec, worker for the build).`,
                description: "Planner loop enforcement",
                metadata: { plannerLoopNudge: true },
                delivery: "queue",
                resume: true,
              } as never)
              .catch((err) => debugLog(`watchdog: loop nudge delivery failed: ${String(err)}`))
          }
        }
        // The configured per-agent models are authoritative: models sometimes
        // inject a hallucinated `model` into the subagent call (e.g.
        // "openai/gpt-5.1-codex-max"), and an unavailable id aborts the
        // delegation. Drop any per-call override.
        const input = e.input as { model?: unknown } | undefined
        if (input && typeof input === "object" && "model" in input) {
          debugLog(`watchdog: dropped model override ${String((input as { model?: unknown }).model)} from a subagent call`)
          delete (input as { model?: unknown }).model
        }
        recordSubagentAttempt(String(e.sessionID), e.input)
      })
    } catch {
      // tool hook unavailable — the watchdog cannot count subagent calls
    }
    try {
      await ctx.tool.hook("execute.after", async (event) => {
        try {
          const e = event as unknown as {
            status?: string
            tool?: string
            id?: string
            input?: { filePath?: string; file_path?: string }
            result?: unknown
            sessionID?: string
          }
          // Release the fallback-cap bookkeeping for a finished subagent call.
          if (e?.tool && DELEGATION_TOOLS.has(e.tool) && typeof e.id === "string") fallbackAdmitted.delete(e.id)
          if (e?.sessionID && plannerSessions.has(String(e.sessionID))) {
            // Watchdog: only a COMPLETED subagent call counts as delegation; the
            // brainstormer's verdict is parsed from its result.
            if (e?.tool && DELEGATION_TOOLS.has(e.tool) && e.status === "completed") {
              recordSubagentCall(String(e.sessionID), e.input)
              recordSubagentResult(String(e.sessionID), e.input, (e as { result?: unknown }).result)
            }
            // The planner must not write files itself, and a turn that ends
            // with a relayed question is legitimately waiting for the user.
            if (e?.status === "completed" && (e?.tool === "write" || e?.tool === "edit" || e?.tool === "patch")) {
              turnOf(String(e.sessionID)).wroteFiles = true
              debugLog(`watchdog: planner wrote a file itself (tool=${e.tool} session=${e.sessionID})`)
            }
            if (e?.status === "completed" && e?.tool === "question") {
              turnOf(String(e.sessionID)).askedUser = true
              debugLog(`watchdog: planner asked the user (session=${e.sessionID})`)
            }
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
      for (const settle of settleTimers.values()) clearTimeout(settle)
      settleTimers.clear()
      watchdogAbort.abort()
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
