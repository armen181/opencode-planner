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
import {
  compareVersions,
  defaultsConfigPath,
  detectVersionStatus,
  fetchLatestVersion,
  globalAgentsDir,
  projectAgentsDir,
  readAgentMarker,
  readCachedLatest,
  readDefaultsConfig,
  readPlannerConfig,
  refreshAgentDefinitions,
  resyncProjectConfig,
  runUpdate,
  setEnforceDelegation,
  setMaxParallel,
  updateDefaults,
  updatePlannerConfig,
  type PlannerConfig,
  type VisionMode,
} from "./core"

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
  // The menu STAYS OPEN: after every action the first page shows again, so
  // tweaking several settings never requires retyping /planner-config. Esc
  // on the menu (or the "Done" option) closes it.
  for (;;) {
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
        {
          title: "Delegation enforcement",
          value: "enforceDelegation",
          description: cfg.enforceDelegation
            ? "on — the plugin resumes turns that skip the subagents"
            : "off — the workflow is prompt-only",
        },
        {
          title: "Max worker→tester cycles",
          value: "maxCycles",
          description: `${cfg.maxCycles}`,
        },
        {
          title: "Re-sync from defaults",
          value: "resync",
          description: "Overwrite this project's settings with the current global defaults",
        },
        {
          title: "Global defaults (new projects)",
          value: "globalDefaults",
          description: defaultsConfigPath(),
        },
        {
          title: "Done — close",
          value: "close",
          description: "Exit the configuration dialog",
        },
      ],
    })
    const role = typeof picked === "string" ? picked : undefined
    if (!role || role === "close") return

    if (role === "vision") {
      await configureVision(context, repo)
      continue
    }
    if (role === "maxParallel") {
      await configureParallel(context, repo)
      continue
    }
    if (role === "enforceDelegation") {
      const applied = setEnforceDelegation(repo, !cfg.enforceDelegation)
      context.ui.toast.show({
        title: "Planner",
        message: `Delegation enforcement ${applied ? "enabled" : "disabled"}.`,
        variant: "success",
      })
      continue
    }
    if (role === "maxCycles") {
      const pickedCycles = await context.ui.dialog.select({
        title: `Max worker→tester cycles (current: ${cfg.maxCycles})`,
        current: String(cfg.maxCycles),
        options: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => ({ title: String(n), value: String(n) })),
      })
      if (pickedCycles === undefined) continue
      updatePlannerConfig(repo, (c) => {
        c.maxCycles = Number(pickedCycles)
      })
      context.ui.toast.show({ title: "Planner", message: `Max cycles: ${Number(pickedCycles)}.`, variant: "success" })
      continue
    }
    if (role === "resync") {
      await resyncWithConfirm(context, repo)
      continue
    }
    if (role === "globalDefaults") {
      await configureGlobalDefaults(context, repo)
      continue
    }

    const current = cfg.models[role as "brainstormer" | "worker" | "tester"]
    const model = await pickModel(context, `Model for the ${role} agent`, current)
    if (!model) continue
    const variant = await pickVariant(context, model, cfg.variants?.[role as "brainstormer" | "worker" | "tester"])
    if (variant === null) continue
    const spec = variant ? `${model}#${variant}` : model
    const text = applyAgentConfig(repo, role, spec)
    context.ui.toast.show({ title: "Planner", message: text, variant: "success" })
  }
}

/** Confirm + replace the project settings with the current global defaults. */
async function resyncWithConfirm(context: AnyContext, repo: string): Promise<void> {
  const picked = await context.ui.dialog.select({
    title: "Re-sync this project from the global defaults?",
    options: [
      {
        title: "Re-sync now",
        value: "yes",
        description: "Overwrite this project's planner settings with the current global defaults",
      },
      { title: "Cancel", value: "no", description: "Keep the project's current settings" },
    ],
  })
  if (picked !== "yes") return
  resyncProjectConfig(repo)
  const cfg = readPlannerConfig(repo)
  context.ui.toast.show({
    title: "Planner",
    message: `Re-synced from ${defaultsConfigPath()} (cycles=${cfg.maxCycles}, parallel=${cfg.maxParallelSubagents}).`,
    variant: "success",
  })
}

/** Edit the GLOBAL defaults (seeds new projects; existing snapshots stay).
 *  Same stay-open menu as /planner-config: after every action the first page
 *  shows again; Esc or "Done" closes it. */
async function configureGlobalDefaults(context: AnyContext, repo: string): Promise<void> {
  for (;;) {
    const cfg = readDefaultsConfig()
    const picked = await context.ui.dialog.select({
      title: "Planner global defaults — pick a setting (applies to new project snapshots)",
      options: [
        { title: "Brainstormer model", value: "brainstormer", description: cfg.models.brainstormer || "(not set — inherit the main model)" },
        { title: "Worker model", value: "worker", description: cfg.models.worker || "(not set — inherit the main model)" },
        { title: "Tester model", value: "tester", description: cfg.models.tester || "(not set — inherit the main model)" },
        {
          title: "Vision",
          value: "vision",
          description: cfg.vision.enabled
            ? `${cfg.vision.model || "(inherit)"}${cfg.vision.variant ? `#${cfg.vision.variant}` : ""} · ${cfg.vision.mode ?? "agent"} mode`
            : "(disabled)",
        },
        { title: "Max parallel subagents", value: "maxParallel", description: String(cfg.maxParallelSubagents) },
        { title: "Max worker→tester cycles", value: "maxCycles", description: String(cfg.maxCycles) },
        {
          title: "Delegation enforcement",
          value: "enforceDelegation",
          description: cfg.enforceDelegation ? "on" : "off",
        },
        {
          title: "Done — close",
          value: "close",
          description: "Exit the dialog",
        },
      ],
    })
    const role = typeof picked === "string" ? picked : undefined
    if (!role || role === "close") return

    if (role === "vision") {
      await configureGlobalVision(context, cfg)
      continue
    }
    if (role === "maxParallel") {
      const v = await context.ui.dialog.select({
        title: `Global defaults — max parallel subagents (current: ${cfg.maxParallelSubagents})`,
        current: String(cfg.maxParallelSubagents),
        options: [1, 2, 3, 4].map((n) => ({
          title: n === 1 ? "1 — strictly serial (recommended)" : `${n} in parallel`,
          value: String(n),
        })),
      })
      if (v === undefined) continue
      updateDefaults((c) => {
        c.maxParallelSubagents = Number(v)
      })
      context.ui.toast.show({ title: "Planner", message: `Default max parallel subagents: ${Number(v)}.`, variant: "success" })
      continue
    }
    if (role === "maxCycles") {
      const v = await context.ui.dialog.select({
        title: `Global defaults — max worker→tester cycles (current: ${cfg.maxCycles})`,
        current: String(cfg.maxCycles),
        options: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => ({ title: String(n), value: String(n) })),
      })
      if (v === undefined) continue
      updateDefaults((c) => {
        c.maxCycles = Number(v)
      })
      context.ui.toast.show({ title: "Planner", message: `Default max cycles: ${Number(v)}.`, variant: "success" })
      continue
    }
    if (role === "enforceDelegation") {
      const applied = updateDefaults((c) => {
        c.enforceDelegation = !c.enforceDelegation
      }).enforceDelegation
      context.ui.toast.show({
        title: "Planner",
        message: `Default delegation enforcement ${applied ? "enabled" : "disabled"}.`,
        variant: "success",
      })
      continue
    }

    const agent = role as "brainstormer" | "worker" | "tester"
    const model = await pickModel(context, `Default model for the ${agent} agent`, cfg.models[agent])
    if (!model) continue
    const variant = await pickVariant(context, model, cfg.variants?.[agent])
    if (variant === null) continue
    updateDefaults((c) => {
      c.models[agent] = model
      if (!c.variants) c.variants = {}
      if (variant) c.variants[agent] = variant
      else delete c.variants[agent]
    })
    context.ui.toast.show({
      title: "Planner",
      message: `Global default: ${agent} → ${model}${variant ? `#${variant}` : ""}. New projects will use it.`,
      variant: "success",
    })
  }
}

/** Vision flow against the GLOBAL defaults file. */
async function configureGlobalVision(context: AnyContext, cfg: PlannerConfig): Promise<void> {
  const current = !cfg.vision.enabled ? "off" : (cfg.vision.mode ?? "agent")
  const picked = await context.ui.dialog.select({
    title: "Global defaults — vision mode",
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
    updateDefaults((c) => {
      c.vision.enabled = false
    })
    context.ui.toast.show({ title: "Planner", message: "Default vision disabled.", variant: "success" })
    return
  }
  updateDefaults((c) => {
    c.vision.enabled = true
    c.vision.mode = mode as VisionMode
  })
  if (mode !== "agent") {
    context.ui.toast.show({ title: "Planner", message: `Default vision mode: ${mode.toUpperCase()}.`, variant: "success" })
    return
  }
  const model = await pickModel(context, "Default vision model", cfg.vision.model)
  if (!model) return
  const variant = await pickVariant(context, model, cfg.vision.variant)
  if (variant === null) return
  updateDefaults((c) => {
    c.vision.model = model
    if (variant) c.vision.variant = variant
    else delete c.vision.variant
  })
  context.ui.toast.show({
    title: "Planner",
    message: `Default vision model: ${model}${variant ? `#${variant}` : ""}.`,
    variant: "success",
  })
}

/** Startup notice: cached update check once per 24h + version mismatches.
 *  Silent when offline; skipped entirely with PLANNER_NO_UPDATE_CHECK=1. */
async function maybeNotifyUpdate(context: AnyContext, repo: string): Promise<void> {
  if (process.env.PLANNER_NO_UPDATE_CHECK === "1") return
  try {
    const check = await fetchLatestVersion(false)
    const status = detectVersionStatus(repo)
    const newer = Boolean(check.latest && compareVersions(check.latest, status.running) > 0)
    if (newer) {
      context.ui.toast.show({
        title: "Planner",
        message: `Update available: v${check.latest} (running v${status.running}) — run /planner-status to update.`,
        variant: "warning",
      })
    } else if (status.mismatch) {
      context.ui.toast.show({
        title: "Planner",
        message: `Planner version conflicts in this project — run /planner-status.`,
        variant: "warning",
      })
    }
  } catch {
    // the notice is best-effort
  }
}

/** Refresh one agents dir; on a legacy install (no marker) whose files differ
 *  from the templates, offer an explicit overwrite (with .bak backups). */
async function refreshAgentsScoped(
  context: AnyContext,
  dir: string,
  label: string,
): Promise<{ updated: number; kept: number; backedUp: number }> {
  const wasLegacy = !readAgentMarker(dir)
  let result = refreshAgentDefinitions(dir)
  if (wasLegacy && result.kept.length > 0) {
    const answer = await context.ui.dialog.select({
      title: `${label}: ${result.kept.length} agent file(s) differ from the current templates (legacy install)`,
      options: [
        {
          title: "Overwrite with backups",
          value: "yes",
          description: `Older or edited files are saved as <agent>.md.bak in ${dir}`,
        },
        { title: "Keep them", value: "no", description: "Leave the existing files untouched" },
      ],
    })
    if (answer === "yes") result = refreshAgentDefinitions(dir, { force: true })
  }
  return { updated: result.updated.length, kept: result.kept.length, backedUp: result.backedUp.length }
}

/** `/planner-status`: versions, conflicts, update check + one-click update. */
async function showPlannerStatus(context: AnyContext, repo: string): Promise<void> {
  const status = detectVersionStatus(repo)
  const cached = readCachedLatest()
  const latestVersion = cached?.latest
  const updateAvailable = Boolean(latestVersion && compareVersions(latestVersion, status.running) > 0)
  const versions = [
    `install ${status.install?.version ? `v${status.install.version}` : "(none)"}`,
    `config ${status.projectConfigVersion ? `v${status.projectConfigVersion}` : "(none)"}`,
    `agents ${status.projectAgentsMarker ? `v${status.projectAgentsMarker.version}` : "(none)"}`,
    `local copy ${status.localPluginVersion ? `v${status.localPluginVersion}` : "(none)"}`,
    `latest ${latestVersion ? `v${latestVersion}` : cached?.error ? "(check failed)" : "(not checked)"}`,
  ].join(" · ")
  const options: Array<{ title: string; value: string; description?: string }> = [
    { title: `Running v${status.running}`, value: "noop", description: versions },
  ]
  if (status.notes.length) {
    options.push({ title: "Version conflicts", value: "noop-conflicts", description: status.notes.join(" · ").slice(0, 220) })
  }
  options.push({
    title: "Check for updates",
    value: "check",
    description: cached?.error ? `last check failed: ${cached.error}` : "query the GitHub repo (read-only)",
  })
  if (updateAvailable || status.mismatch) {
    options.push({
      title: `Update now${latestVersion ? ` → v${latestVersion}` : ""}`,
      value: "update",
      description: "git pull --ff-only + install.sh --yes from the recorded source clone; restart required",
    })
  }
  options.push({
    title: "Refresh agent definitions",
    value: "agents",
    description: "update the files this plugin wrote; user-edited files are kept",
  })
  const picked = await context.ui.dialog.select({ title: "Planner status", options })
  const action = typeof picked === "string" ? picked : undefined
  if (!action || action.startsWith("noop")) return

  if (action === "check") {
    const result = await fetchLatestVersion(true)
    context.ui.toast.show({
      title: "Planner",
      message: result.latest
        ? `Latest on GitHub: v${result.latest}${compareVersions(result.latest, status.running) > 0 ? " — update available" : " — up to date"}.`
        : `Update check failed: ${result.error ?? "unknown error"}`,
      variant: result.latest ? "success" : "warning",
    })
    return
  }

  if (action === "agents") {
    const project = await refreshAgentsScoped(context, projectAgentsDir(repo), "Project agents")
    const global = await refreshAgentsScoped(context, globalAgentsDir(), "Global agents")
    context.ui.toast.show({
      title: "Planner",
      message:
        `Agent definitions refreshed — project: ${project.updated} updated, ${project.kept} kept` +
        `${project.backedUp ? `, ${project.backedUp} backed up` : ""}; ` +
        `global: ${global.updated} updated, ${global.kept} kept${global.backedUp ? `, ${global.backedUp} backed up` : ""}.`,
      variant: "success",
    })
    return
  }

  if (action === "update") {
    const confirmed = await context.ui.dialog.select({
      title: "Update the planner plugin?",
      options: [
        {
          title: "Update now",
          value: "yes",
          description: `git pull + install.sh in ${status.install?.sourceRoot ?? "(no source clone recorded)"}`,
        },
        { title: "Cancel", value: "no", description: "Keep the current version" },
      ],
    })
    if (confirmed !== "yes") return
    const logPath = `/tmp/planner-update-${Date.now()}.log`
    context.ui.toast.show({ title: "Planner", message: "Updating — git pull + install.sh are running…", variant: "info" })
    const result = await runUpdate(status.install?.sourceRoot ?? "", logPath)
    // Bring agent definitions in line with the freshly installed templates.
    const project = await refreshAgentsScoped(context, projectAgentsDir(repo), "Project agents")
    const global = await refreshAgentsScoped(context, globalAgentsDir(), "Global agents")
    if (result.ok) {
      context.ui.toast.show({
        title: "Planner",
        message:
          `Updated. Restart OpenCode to apply. Agents: ${project.updated + global.updated} refreshed, ` +
          `${project.kept + global.kept} kept (edits)${project.backedUp + global.backedUp ? `, ${project.backedUp + global.backedUp} backed up` : ""}. Log: ${logPath}`,
        variant: "success",
      })
    } else {
      context.ui.toast.show({
        title: "Planner",
        message: `Update failed: ${result.error ?? "see log"} — ${logPath}`,
        variant: "error",
      })
    }
    return
  }
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
    // One-time update/version notice per session (delayed so the UI is mounted).
    if (repo) setTimeout(() => void maybeNotifyUpdate(context, repo), 3000)

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
            id: "planner.status",
            title: "Planner: status & updates",
            group: "Planner",
            palette: true,
            slash: { name: "planner-status" },
            enabled: () => !!repo,
            run: async () => {
              if (!repo) return
              await showPlannerStatus(context, repo)
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
