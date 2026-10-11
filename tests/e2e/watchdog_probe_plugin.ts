/**
 * Test-only helper plugin for the R2 delegation-watchdog live suite.
 *
 * While the file pointed to by WATCHDOG_ARM_FILE exists, `subagent` tool calls
 * of a planner session FAIL until the watchdog has nudged that session (its
 * nudge line appears in PLANNER_DEBUG_FILE) — so the first turn cannot contain
 * a COMPLETED delegation and a nudge is guaranteed; once nudged, the resumed
 * turn's call runs for real.
 *
 * While WATCHDOG_NEVER_ARM_FILE exists, calls fail unconditionally — used to
 * exercise the 2-nudge cap (a session that never recovers).
 *
 * Copied into the scratch project's .opencode/plugins/ as a single-file server
 * plugin by tests/e2e/test_delegation_live.py. Never shipped.
 */
import { appendFileSync, existsSync, readFileSync } from "node:fs"

const sink = process.env.PLANNER_DEBUG_FILE ?? ""
const log = (...a: unknown[]) => {
  const line = `[watchdog-probe] ${a.map(String).join(" ")}`
  console.error(line)
  if (sink) {
    try {
      appendFileSync(sink, line + "\n")
    } catch {
      // best-effort
    }
  }
}

/** True once the planner watchdog has nudged this session (debug line). */
const nudged = (sid: string): boolean => {
  if (!sink || !sid) return false
  try {
    const text = readFileSync(sink, "utf8")
    return text.split("\n").some((ln) => ln.includes("watchdog: nudge") && ln.includes(sid))
  } catch {
    return false
  }
}

export default {
  id: "watchdog-probe",
  async setup(ctx: any) {
    const armFile = process.env.WATCHDOG_ARM_FILE ?? ""
    const neverArmFile = process.env.WATCHDOG_NEVER_ARM_FILE ?? ""
    log(`armed-file=${armFile || "(unset)"} never-file=${neverArmFile || "(unset)"}`)
    try {
      await ctx.tool.transform((editor: any) => {
        editor.update("subagent", (tool: any) => {
          const current = tool.execute
          if (typeof current !== "function") return
          if ((current as { __watchdogProbe?: boolean }).__watchdogProbe) return
          const original = current as (input: unknown, context: unknown) => Promise<unknown>
          const wrapped = async (input: unknown, context: { sessionID?: string; agent?: string } | undefined) => {
            const sid = String(context?.sessionID ?? "")
            const plannerCall = !context?.agent || context.agent === "planner"
            const never = Boolean(neverArmFile) && existsSync(neverArmFile)
            const armed = Boolean(armFile) && existsSync(armFile)
            if (plannerCall && (never || (armed && !nudged(sid)))) {
              log(`failing subagent call for session=${sid} (${never ? "never-recover" : "no nudge yet"})`)
              throw new Error("watchdog-probe: subagent tool temporarily unavailable (test)")
            }
            return original.call(tool, input, context)
          }
          ;(wrapped as { __watchdogProbe?: boolean }).__watchdogProbe = true
          tool.execute = wrapped as typeof tool.execute
        })
      })
    } catch (e) {
      log(`tool transform failed: ${String(e)}`)
    }
  },
}
