/**
 * Planner command operations — used by the TUI dialogs. All state lives in the
 * single `.opencode/planner-plugin.json` file; the server plugin only watches
 * that file and reloads agents.
 */
import {
  CONFIGURABLE_AGENTS,
  formatModelSpec,
  parseModelSpec,
  setAgentModel,
  setAgentVariant,
  setVisionEnabled,
  setVisionMode,
  setVisionModel,
  setVisionVariant,
} from "./core"

/** Apply the "configure an agent" dialog result. Returns the reply text. */
export function applyAgentConfig(repo: string, agent: string, spec: string): string {
  const parsed = parseModelSpec(spec)
  if (!parsed) {
    return `Invalid model id: ${spec}. Expected provider/model-id[#effort] (e.g. anthropic/claude-sonnet#high).`
  }
  const modelStr = formatModelSpec(parsed.providerID, parsed.modelID)
  if (agent === "vision") {
    setVisionModel(repo, modelStr)
    setVisionVariant(repo, parsed.variant)
    return (
      `Vision model set to ${modelStr}` +
      (parsed.variant ? ` with effort ${parsed.variant}` : "") +
      `. Applies to all sessions in this project.`
    )
  }
  if (agent === "planner") {
    return (
      `The planner uses opencode's main model — select it with /model. ` +
      `Configure brainstormer, worker, tester or vision instead.`
    )
  }
  if ((CONFIGURABLE_AGENTS as readonly string[]).includes(agent)) {
    const ag = agent as (typeof CONFIGURABLE_AGENTS)[number]
    setAgentModel(repo, ag, modelStr)
    setAgentVariant(repo, ag, parsed.variant)
    return (
      `${agent} model set to ${modelStr}` +
      (parsed.variant ? ` with effort ${parsed.variant}` : "") +
      `. Applies to all sessions in this project.`
    )
  }
  return `Unknown agent: ${agent}. Expected one of: ${CONFIGURABLE_AGENTS.join(", ")}, vision`
}

/** Apply the "configure vision" dialog result. Returns the reply text. */
export function applyVisionConfig(repo: string, what: string): string {
  const value = what.trim().toLowerCase()
  if (value === "on" || value === "enable") {
    setVisionEnabled(repo, true)
    return "Vision enabled. Applies to all sessions in this project."
  }
  if (value === "off" || value === "disable") {
    setVisionEnabled(repo, false)
    return "Vision disabled. Applies to all sessions in this project."
  }
  if (value === "proxy" || value === "bridge") {
    setVisionEnabled(repo, true)
    setVisionMode(repo, "proxy")
    return (
      "Vision mode: PROXY — the upstream proxy transcribes images in-flight, " +
      "so agents Read images directly (no vision subagent)."
    )
  }
  if (value === "agent") {
    setVisionEnabled(repo, true)
    setVisionMode(repo, "agent")
    return "Vision mode: AGENT — planner delegates image tasks to the vision subagent."
  }
  if (value === "native") {
    setVisionEnabled(repo, true)
    setVisionMode(repo, "native")
    return (
      "Vision mode: NATIVE — the planner/worker/tester models themselves are image-capable; " +
      "agents Read images directly. Make sure every agent model actually supports image input."
    )
  }
  return `Unknown vision mode: ${what}. Expected on, off, agent, proxy or native.`
}
