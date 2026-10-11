/**
 * Unit harness for the tool-name-alias plugin's input normalization
 * (extras/tool-name-aliases.js) — in particular the `question` tool shape:
 * a bare `question` string must yield the REAL enumerated alternatives as
 * options, never invented Yes/No.
 * Run via tests/e2e/test_alias_unit.py (bun or node).
 */
import { normalizeInput, normalizeQuestionInput, deriveOptionsFromText, mcpAliasNames, ALIASES } from "../../extras/tool-name-aliases.js"

const results: Array<[string, boolean]> = []
function check(name: string, ok: boolean, detail = ""): void {
  results.push([name, Boolean(ok)])
  console.log(`  ${ok ? "PASS" : "FAIL"}: ${name}${ok ? "" : ` — ${detail}`}`)
}

// 1. Letter enumeration in a bare question → the real alternatives, no Yes/No.
{
  const out = normalizeQuestionInput({
    question: "Where should the version string come from? A) git tag B) VERSION file C) hardcoded constant",
  }) as { questions: Array<{ question: string; header: string; options: Array<{ label: string; description: string }> }> }
  const labels = (out.questions?.[0]?.options ?? []).map((o) => o.label)
  check("letter enumeration -> real options", JSON.stringify(labels) === JSON.stringify(["git tag", "VERSION file", "hardcoded constant"]), JSON.stringify(labels))
  check("no invented Yes/No options", !labels.includes("Yes") && !labels.includes("No"))
  check("header defaulted", out.questions?.[0]?.header === "Question")
}

// 2. Numbered line enumeration.
{
  const out = normalizeQuestionInput({
    question: "Which storage backend?\n1. SQLite - embedded, zero setup\n2. Postgres — needs a server",
  }) as { questions: Array<{ options: Array<{ label: string; description: string }> }> }
  const opts = out.questions?.[0]?.options ?? []
  check(
    "numbered lines -> options with descriptions",
    opts.length === 2 && opts[0].label === "SQLite" && opts[0].description.includes("embedded") && opts[1].label === "Postgres",
    JSON.stringify(opts),
  )
}

// 3. "or" list with a mid-sentence question mark.
{
  const out = normalizeQuestionInput({ question: "What timeout should the client use? 30s or 60s?" }) as {
    questions: Array<{ options: Array<{ label: string }> }>
  }
  const labels = (out.questions?.[0]?.options ?? []).map((o) => o.label)
  check("or-list -> both alternatives", JSON.stringify(labels) === JSON.stringify(["30s", "60s"]), JSON.stringify(labels))
}

// 4. Explicit string options pass through as label objects (binary stays binary).
{
  const out = normalizeQuestionInput({ question: "Deploy now?", options: ["yes", "no"] }) as {
    questions: Array<{ options: Array<{ label: string; description: string }> }>
  }
  const opts = out.questions?.[0]?.options ?? []
  check("string options -> label objects", opts.length === 2 && opts[0].label === "yes" && opts[1].label === "no", JSON.stringify(opts))
}

// 5. Native shape is preserved.
{
  const native = {
    questions: [{ question: "Which DB?", header: "Storage", options: [{ label: "SQLite", description: "embedded" }] }],
  }
  const out = normalizeQuestionInput(native) as typeof native
  check(
    "native questions array preserved",
    out.questions?.[0]?.header === "Storage" && out.questions?.[0]?.options?.[0]?.label === "SQLite",
    JSON.stringify(out),
  )
}

// 6. Long header truncated to 30 chars.
{
  const out = normalizeQuestionInput({
    questions: [{ question: "q", header: "This header is far longer than thirty characters", options: [] }],
  }) as { questions: Array<{ header: string }> }
  check("header truncated to 30", (out.questions?.[0]?.header ?? "").length <= 30, out.questions?.[0]?.header)
}

// 7. No enumeration -> empty options (free-text answer), never forced Yes/No.
{
  const out = normalizeQuestionInput({ question: "Describe the desired behaviour in your own words." }) as {
    questions: Array<{ options: unknown[] }>
  }
  const opts = out.questions?.[0]?.options ?? []
  check("no enumeration -> free-text (empty options)", Array.isArray(opts) && opts.length === 0, JSON.stringify(opts))
}

// 8. Ordinary key aliasing still works (read: file_path -> path, the 2.0.x name).
{
  const out = normalizeInput("read", { file_path: "/tmp/x.md" }) as Record<string, unknown>
  check("read file_path -> path", out.path === "/tmp/x.md" && !("file_path" in out), JSON.stringify(out))
}

// 9. list_dir bare path -> glob pattern.
{
  const out = normalizeInput("glob", { path: "/tmp/proj" }) as Record<string, unknown>
  check("list_dir path -> glob pattern", out.pattern === "*" && out.path === "/tmp/proj", JSON.stringify(out))
}

// 10. deriveOptionsFromText: comma list capped at 4, deduped.
{
  const opts = deriveOptionsFromText("Which color: red, red, green, blue, or yellow, purple?")
  const labels = opts.map((o) => o.label)
  check("comma list deduped + capped", labels.length === 4 && labels[0] === "red" && new Set(labels).size === 4, JSON.stringify(labels))
}

// 11. mcp__-prefixed hallucinated names are covered.
{
  const names = mcpAliasNames().map(([n]) => n)
  check("mcp__local__write_file registered", names.includes("mcp__local__write_file"), JSON.stringify(names))
  check("mcp__local__exec registered", names.includes("mcp__local__exec"), JSON.stringify(names))
  check("mcp__local__glob_file_search registered", names.includes("mcp__local__glob_file_search"), JSON.stringify(names))
  const out = normalizeInput("write", { path: "/tmp/spec.md", content: "hi" }) as Record<string, unknown>
  check("write path/content passes through", out.path === "/tmp/spec.md" && out.content === "hi", JSON.stringify(out))
}

// 11b. spawn_agent/task-shaped subagent calls normalize to the native schema.
{
  const a = normalizeInput("subagent", { subagent: "vision", prompt: "Analyze the image.\nMore detail." }) as Record<string, unknown>
  check("spawn_agent subagent->agent", a.agent === "vision", JSON.stringify(a).slice(0, 120))
  check("description derived from the prompt", a.description === "Analyze the image.", String(a.description))
  const b = normalizeInput("subagent", { agent: "worker", task: "Build the level", prompt: "build" }) as Record<string, unknown>
  check("task -> description", b.description === "Build the level", JSON.stringify(b).slice(0, 120))
  check("plain exec alias maps to shell", (ALIASES as Record<string, string>).exec === "shell")
}

// 12. The plugin's setup() actually registers the mcp-style tools and their
// execute delegates to the canonical tool with normalized input.
{
  const plugin = (await import("../../extras/tool-name-aliases.js")).default
  const added: Record<string, { execute: (input: unknown, ctx: unknown) => Promise<unknown> }> = {}
  const known = ["read", "write", "edit", "shell", "glob", "grep", "question", "subagent"]
  const editor = {
    get: (name: string) => (known.includes(name) ? { execute: async () => ({}) } : undefined),
    add: (tool: { name: string; execute: (input: unknown, ctx: unknown) => Promise<unknown> }) => {
      added[tool.name] = tool
    },
  }
  let disposed = false
  const fakeCtx = {
    tool: {
      transform: async (fn: (e: unknown) => void) => fn(editor),
      hook: async () => ({ dispose: () => (disposed = true) }),
      list: async () => known.map((id) => ({ id })),
    },
  }
  const cleanup = await (plugin as { setup: (ctx: unknown) => Promise<() => void> }).setup(fakeCtx)
  const mcpNames = Object.keys(added).filter((n) => n.startsWith("mcp__"))
  check("setup registers mcp__local__write_file", "mcp__local__write_file" in added, JSON.stringify(mcpNames))
  check("setup registers mcp__local__read_file", "mcp__local__read_file" in added, JSON.stringify(mcpNames))
  // The registered execute must delegate to the canonical tool with the
  // normalized input (file_path -> path).
  let seen: unknown
  known.length = 0
  known.push("read", "write", "edit", "shell", "glob", "grep", "question", "subagent")
  const target = { id: "write", execute: async (input: unknown) => (seen = input) }
  const withTarget = {
    ...fakeCtx,
    tool: { ...fakeCtx.tool, list: async () => [target] },
  }
  // re-run setup against a registry whose write tool records its input
  const added2: Record<string, { execute: (input: unknown, ctx: unknown) => Promise<unknown> }> = {}
  const editor2 = {
    get: (name: string) => (name === "write" ? target : undefined),
    add: (tool: { name: string; execute: (input: unknown, ctx: unknown) => Promise<unknown> }) => {
      added2[tool.name] = tool
    },
  }
  await (plugin as { setup: (ctx: unknown) => Promise<() => void> }).setup({
    ...withTarget,
    tool: { ...withTarget.tool, transform: async (fn: (e: unknown) => void) => fn(editor2) },
  })
  await added2["mcp__local__write_file"].execute({ path: "/tmp/a.md", content: "x" }, {})
  check("mcp alias delegates to canonical write", Boolean(seen), JSON.stringify(seen))
  await cleanup()
  check("setup cleanup disposes the hook registration", disposed)
}

const failed = results.filter(([, ok]) => !ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed`)
if (failed.length) {
  console.error("FAILED checks:", failed.map(([n]) => n).join("; "))
  process.exit(1)
}
