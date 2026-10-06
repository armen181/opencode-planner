/**
 * Planner plugin — OpenCode V2 CLI (TUI) implementation.
 *
 * Registers the planner configuration dialogs. `/planner-config` (typed in
 * the TUI) opens the config dialog: pick brainstormer/worker/tester/vision to
 * set a model + effort variant, or max-parallel subagents. Vision and
 * parallelism also have palette (ctrl+p) entries. The server owns no
 * `/planner-config` command, so the name never appears twice. Writes go to
 * the same `.opencode/planner-plugin.json` the server plugin watches, so
 * changes apply live without a restart.
 *
 * Exported as `./tui` from package.json — OpenCode loads it automatically for
 * the terminal UI (a plugin directory keeps server and TUI entrypoints
 * together).
 */
import { applyAgentConfig, applyVisionConfig } from "./commands"
import { readPlannerConfig, setMaxParallel } from "./core"

/** `Plugin.define` is an identity helper; avoiding the runtime import keeps
 *  the TUI plugin loadable from any discovered directory with zero deps. */
function define<P>(plugin: P): P {
  return plugin
}

type AnyContext = any

const DEFAULT_VARIANT = "(default)"

function repoOf(context: AnyContext): string | undefined {
  return context?.location?.directory ?? context?.data?.location?.default?.()?.directory
}

function modelEntries(context: AnyContext): Array<Record<string, unknown>> {
  try {
    const list = context.data.location.model.list(context.location) ?? []
    return Array.isArray(list) ? (list as Array<Record<string, unknown>>) : []
  } catch {
    return []
  }
}

function variantsFor(context: AnyContext, value: string): string[] {
  const entry = modelEntries(context).find((m) => `${m.providerID}/${m.modelID ?? m.id}` === value)
  const variants = Array.isArray(entry?.variants) ? (entry?.variants as unknown[]) : []
  return variants
    .map((v) => (typeof v === "string" ? v : (v as { id?: unknown })?.id))
    .filter((v): v is string => typeof v === "string" && v.length > 0)
}

async function pickModel(context: AnyContext, title: string, current?: string): Promise<string | undefined> {
  const options = modelEntries(context)
    .filter((m) => m?.providerID && (m?.modelID ?? m?.id))
    .map((m) => {
      const value = `${m.providerID}/${m.modelID ?? m.id}`
      return {
        title: value,
        value,
        description: typeof m.name === "string" ? m.name : undefined,
        category: String(m.providerID),
      }
    })
  if (options.length === 0) {
    context.ui.toast.show({ title: "Planner", message: "No models available in this installation.", variant: "warning" })
    return undefined
  }
  const currentBase = current?.split("#")[0]
  const picked = await context.ui.dialog.select({
    title,
    current: currentBase,
    options,
    // Type-to-filter: the list contains every model of every configured
    // provider, so searching by provider/model (or provider name) is what
    // makes the dialog usable.
    search: (query: string, opts: Array<{ title: string; category?: string }>) => {
      const q = query.trim().toLowerCase()
      if (!q) return opts
      return opts.filter(
        (o) => `${o.title ?? ""}`.toLowerCase().includes(q) || `${o.category ?? ""}`.toLowerCase().includes(q),
      )
    },
  })
  return typeof picked === "string" && picked.length ? picked : undefined
}

async function pickVariant(
  context: AnyContext,
  model: string,
  current?: string,
): Promise<string | undefined | null> {
  const variants = variantsFor(context, model)
  if (variants.length === 0) return undefined
  const picked = await context.ui.dialog.select({
    title: `Effort variant for ${model}`,
    current: current ?? DEFAULT_VARIANT,
    options: [
      { title: DEFAULT_VARIANT, value: DEFAULT_VARIANT, description: "Use the model's default effort" },
      ...variants.map((v) => ({ title: v, value: v })),
    ],
  })
  if (picked === undefined) return null // cancelled
  return picked === DEFAULT_VARIANT ? undefined : String(picked)
}

async function configureAgent(context: AnyContext, repo: string): Promise<void> {
  const cfg = readPlannerConfig(repo)
  // The planner is not configurable here: it always follows opencode's main
  // model (selected with /model), so the session's own choice governs it.
  const picked = await context.ui.dialog.select({
    title: "Planner config — pick an agent (planner uses /model)",
    options: [
      {
        title: "Brainstormer (subagent)",
        value: "brainstormer",
        description: cfg.models.brainstormer || "(not set — inherits the main model)",
      },
      { title: "Worker (subagent)", value: "worker", description: cfg.models.worker || "(not set — inherits the main model)" },
      { title: "Tester (subagent)", value: "tester", description: cfg.models.tester || "(not set — inherits the main model)" },
      {
        title: "Vision (subagent)",
        value: "vision",
        description: cfg.vision.enabled
          ? `${cfg.vision.model || "(inherit)"}${cfg.vision.variant ? `#${cfg.vision.variant}` : ""} · ${cfg.vision.mode ?? "agent"} mode`
          : "(disabled)",
      },
      {
        title: "Max parallel subagents",
        value: "maxParallel",
        description: `${cfg.maxParallelSubagents}${cfg.maxParallelSubagents <= 1 ? " — strictly serial (recommended)" : " in parallel"}`,
      },
    ],
  })
  const role = typeof picked === "string" ? picked : undefined
  if (!role) return

  if (role === "vision") {
    await configureVision(context, repo)
    return
  }
  if (role === "maxParallel") {
    await configureParallel(context, repo)
    return
  }

  const current = cfg.models[role as "brainstormer" | "worker" | "tester"]
  const model = await pickModel(context, `Model for the ${role} agent`, current)
  if (!model) return
  const variant = await pickVariant(context, model, cfg.variants?.[role as "brainstormer" | "worker" | "tester"])
  if (variant === null) return
  const spec = variant ? `${model}#${variant}` : model
  const text = applyAgentConfig(repo, role, spec)
  context.ui.toast.show({ title: "Planner", message: text, variant: "success" })
}

/** Subagent parallelism picker: 1 = strictly serial (default). */
async function configureParallel(context: AnyContext, repo: string): Promise<void> {
  const cfg = readPlannerConfig(repo)
  const picked = await context.ui.dialog.select({
    title: `Max parallel subagents (current: ${cfg.maxParallelSubagents})`,
    current: String(cfg.maxParallelSubagents),
    options: [1, 2, 3, 4].map((n) => ({
      title: n === 1 ? "1 — strictly serial (recommended for local models)" : `${n} in parallel`,
      value: String(n),
      description:
        n === 1
          ? "One subagent task at a time; the next starts after the previous returns."
          : `Allow up to ${n} subagent tasks at once (needs a server that handles concurrency).`,
    })),
  })
  if (picked === undefined) return
  const applied = setMaxParallel(repo, Number(picked))
  context.ui.toast.show({
    title: "Planner",
    message: `Max parallel subagents: ${applied}${applied <= 1 ? " — tasks run strictly serially." : ""}`,
    variant: "success",
  })
}

async function configureVision(context: AnyContext, repo: string): Promise<void> {
  const cfg = readPlannerConfig(repo)
  const current = !cfg.vision.enabled ? "off" : (cfg.vision.mode ?? "agent")
  const picked = await context.ui.dialog.select({
    title: "Vision mode",
    current,
    options: [
      { title: "Agent", value: "agent", description: "Dedicated vision subagent (multimodal model) reads images" },
      { title: "Proxy", value: "proxy", description: "Upstream proxy transcribes images in-flight; agents Read files" },
      { title: "Native", value: "native", description: "The agents' own models are image-capable" },
      { title: "Off", value: "off", description: "Disable vision handling" },
    ],
  })
  const mode = typeof picked === "string" ? picked : undefined
  if (!mode) return

  if (mode === "off") {
    applyVisionConfig(repo, "off")
    context.ui.toast.show({ title: "Planner", message: "Vision disabled.", variant: "success" })
    return
  }
  applyVisionConfig(repo, mode)
  if (mode === "agent") {
    const model = await pickModel(context, "Vision model", cfg.vision.model)
    if (!model) return
    const variant = await pickVariant(context, model, cfg.vision.variant)
    if (variant === null) return
    const spec = variant ? `${model}#${variant}` : model
    const text = applyAgentConfig(repo, "vision", spec)
    context.ui.toast.show({ title: "Planner", message: text, variant: "success" })
  } else {
    context.ui.toast.show({
      title: "Planner",
      message: `Vision mode: ${mode.toUpperCase()}.`,
      variant: "success",
    })
  }
}

export const PlannerTuiPlugin = define({
  id: "planner.tui",
  setup(context: AnyContext) {
    const repo = repoOf(context)

    // The keymap provider is only mounted after the app renders, so the layer
    // is registered lazily from an `app` slot — calling keymap.layer() during
    // setup fails with "Keymap.Provider is missing" on some builds.
    let disposeLayer: undefined | (() => void)
    const registerLayer = () => {
      if (disposeLayer) return null
      const dispose = context.keymap.layer(() => ({
        mode: "global",
        priority: 10,
        commands: [
          // /planner-config is the single planner slash command — it opens
          // this dialog. The remaining dialogs are palette-only (ctrl+p) so
          // no command ever appears twice.
          {
            id: "planner.config",
            title: "Planner: configure models",
            group: "Planner",
            palette: true,
            slash: { name: "planner-config" },
            enabled: () => !!repo,
            run: async () => {
              if (!repo) return
              await configureAgent(context, repo)
            },
          },
          {
            id: "planner.parallel",
            title: "Planner: subagent parallelism",
            group: "Planner",
            palette: true,
            enabled: () => !!repo,
            run: async () => {
              if (!repo) return
              await configureParallel(context, repo)
            },
          },
          {
            id: "planner.vision",
            title: "Planner: configure vision",
            group: "Planner",
            palette: true,
            enabled: () => !!repo,
            run: async () => {
              if (!repo) return
              await configureVision(context, repo)
            },
          },
        ],
        bindings: [],
      }))
      if (typeof dispose === "function") disposeLayer = dispose
      return null
    }

    const disposeSlot = context.ui.slot({ append: "app", render: registerLayer })

    return () => {
      try {
        if (typeof disposeSlot === "function") disposeSlot()
      } catch {
        // slot dispose is best-effort
      }
      try {
        if (disposeLayer) disposeLayer()
      } catch {
        // layer dispose is best-effort
      }
    }
  },
})

export default PlannerTuiPlugin
