/**
 * tool-name-aliases — makes Claude/Command Code-style tool names work.
 *
 * Local and third-party models habitually call tools that do not exist in
 * OpenCode: `bash`, `ask_user`, `list_dir`, `read_file`, `write_file`,
 * `edit_file`, … (OpenCode's wire names are `shell`, `question`, no listing
 * tool, `read`, `write`, `edit`). The harness answers "No tool named X is
 * currently available" and the session soft-locks.
 *
 * This plugin registers every known alias as a real tool whose execute
 * resolves the canonical OpenCode tool and delegates to it, and also rewrites
 * alias names in the execute.before hook as a second line of defense.
 *
 * An alias is only registered when its canonical tool exists in this session —
 * a missing canonical must surface as the model simply not having the alias,
 * never as a "maps to Y, which is not available" hard error.
 */

const ALIASES = {
  bash: "shell",
  shell_command: "shell",
  run_command: "shell",
  execute_command: "shell",
  run_terminal_cmd: "shell",
  terminal_command: "shell",
  exec: "shell",
  run: "shell",
  try_execute: "shell",
  grep_files: "grep",
  list: "glob",
  ask_user: "question",
  ask_user_question: "question",
  ask_followup_question: "question",
  read_file: "read",
  read_multiple_files: "read",
  view_file: "read",
  write_file: "write",
  create_file: "write",
  save_file: "write",
  edit_file: "edit",
  apply_patch: "patch",
  grep_search: "grep",
  search_files: "grep",
  search_file_content: "grep",
  find_files: "glob",
  list_dir: "glob",
  list_directory: "glob",
  list_files: "glob",
  ls: "glob",
  dir: "glob",
  task: "subagent",
  spawn_agent: "subagent",
  web_search: "websearch",
  web_fetch: "webfetch",
};

// Per-alias input normalisation before delegating to the canonical tool.
// The file tools are normalized to `path` — the name the running 2.0.x builds
// accept (verified live: read/write complete with {"path": ...}).
// subagent: models call it with {subagent|subagent_type|name, task|title,
// prompt} — the native schema wants {agent, description, prompt}.
const KEY_ALIASES = {
  read: { file_path: "path", filepath: "path", filePath: "path", absolute_path: "path" },
  write: { file_path: "path", filepath: "path", filePath: "path" },
  edit: { file_path: "path", filepath: "path", filePath: "path", old_string: "oldString", new_string: "newString", replace_all: "replaceAll" },
  grep: { query: "pattern", regex: "pattern" },
  shell: { cmd: "command", workdir: "cwd" },
  subagent: { subagent: "agent", subagent_type: "agent", name: "agent", task: "description", title: "description" },
};

// Per-alias usage hints so a model calling the alias sees the shape the
// canonical tool expects (its own schema is intentionally loose).
const SHAPE_HINTS = {
  question:
    'Pass {"questions": [{"question": "...", "header": "max 30 chars", "options": [{"label": "1-5 words", "description": "meaning"}, ...2-4 real alternatives]}]}. Every decision question gets its REAL alternatives as options — never yes/no for an open decision. The user can always type a custom answer.',
  write: 'Pass {"path": "...", "content": "..."}.',
  edit: 'Pass {"path": "...", "oldString": "...", "newString": "...", "replaceAll": false}.',
  read: 'Pass {"path": "..."}.',
  shell: 'Pass {"command": "...", "cwd": "..."}.',
};

/**
 * Claude-Code-style MCP names that small models hallucinate on local setups:
 * `mcp__<server>__<tool>` with a server name that does not exist ("local" is
 * the observed one). The harness answers "No tool named X is currently
 * available" and a small model can spiral. The execute.before hook never sees
 * unknown tool names (the engine rejects them before any plugin hook), so the
 * only fix is to REGISTER the mcp-prefixed names as real tools. Kept to a
 * tight set so the tool list stays small for limited-context models.
 */
const MCP_HALLUCINATED_SERVERS = ["local"];
const MCP_BASE_ALIASES = {
  read_file: "read",
  read_multiple_files: "read",
  write_file: "write",
  create_file: "write",
  edit_file: "edit",
  exec: "shell",
  execute_command: "shell",
  run_command: "shell",
  glob_file_search: "glob",
  list_dir: "glob",
};

function mcpAliasNames() {
  const out = [];
  for (const server of MCP_HALLUCINATED_SERVERS) {
    for (const base of Object.keys(MCP_BASE_ALIASES)) {
      out.push([`mcp__${server}__${base}`, MCP_BASE_ALIASES[base]]);
    }
  }
  return out;
}

const log = (...a) => console.error("[tool-name-aliases]", ...a);

function normalizeInput(canonical, input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) return input ?? {};
  if (canonical === "question") return normalizeQuestionInput(input);
  const out = { ...input };
  const map = KEY_ALIASES[canonical] ?? {};
  for (const [from, to] of Object.entries(map)) {
    if (out[to] === undefined && out[from] !== undefined) out[to] = out[from];
    delete out[from];
  }
  // list_dir/ls → glob: a bare path becomes a "*" search in that directory.
  if (canonical === "glob" && out.pattern === undefined) {
    if (typeof out.path === "string") out.pattern = "*";
    else if (typeof out.directory === "string") { out.pattern = "*"; out.path = out.directory; delete out.directory; }
    else out.pattern = "*";
  }
  // subagent: derive a short description from the prompt when none arrived.
  if (canonical === "subagent" && out.description === undefined && typeof out.prompt === "string") {
    out.description = (out.prompt.trim().split(/\r?\n/)[0] || "Delegated task").slice(0, 80);
  }
  return out;
}

/**
 * OpenCode's question tool takes
 *   questions: [{ question, header, options: [{label, description}], multiple? }]
 * and the TUI always adds a "Type your own answer" free-text choice, so a
 * question with NO options is fine. Models calling `ask_user` pass a bare
 * `question` string (often with the alternatives embedded) — derive the real
 * options from the text instead of inventing Yes/No.
 */
function normalizeQuestionInput(input) {
  const out = input && typeof input === "object" && !Array.isArray(input) ? { ...input } : {};
  if (Array.isArray(out.questions)) {
    out.questions = out.questions.map(normalizeOneQuestion).filter(Boolean);
    return out;
  }
  const text =
    (typeof out.question === "string" && out.question) ||
    (typeof out.questions === "string" && out.questions) ||
    (typeof out.prompt === "string" && out.prompt) ||
    (typeof out.query === "string" && out.query) ||
    "";
  if (!text) return out; // unknown shape — pass through untouched
  delete out.question;
  delete out.prompt;
  delete out.query;
  const options = Array.isArray(out.options) ? out.options : undefined;
  delete out.options;
  out.questions = [normalizeOneQuestion({ question: text, options })];
  return out;
}

function normalizeOneQuestion(q) {
  if (typeof q === "string") q = { question: q };
  if (!q || typeof q !== "object") return null;
  const out = { ...q };
  if (typeof out.question !== "string" && typeof out.text === "string") out.question = out.text;
  if (typeof out.question !== "string") return null;
  if (typeof out.header !== "string" || !out.header.trim()) out.header = "Question";
  else if (out.header.length > 30) out.header = out.header.slice(0, 30).trim();
  let options = Array.isArray(out.options) ? out.options : undefined;
  if (options) {
    options = options
      .map((o) => (typeof o === "string" ? { label: o.trim(), description: "" } : o))
      .filter((o) => o && typeof o.label === "string" && o.label.trim());
  } else {
    options = deriveOptionsFromText(out.question);
  }
  out.options = options;
  return out;
}

/**
 * Parse enumerated alternatives out of a question string. Handles:
 *   "Which source? A) git tag B) VERSION file C) hardcode"
 *   "1. git tag\n2. VERSION file\n3. hardcode"
 *   "- git tag\n- VERSION file"
 *   "git tag, VERSION file, or hardcode?"
 * Each item may carry an explanation after " - " / " — " / ": ".
 * Returns [] when no clear enumeration exists (free-text answer then).
 */
function deriveOptionsFromText(text) {
  if (typeof text !== "string") return [];
  const items = [];
  // Line/bullet enumerations.
  const lines = text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  const marked = lines.filter((s) => /^(?:[-*•]|\d{1,2}[.)]|[a-dA-D][.)])\s+/.test(s));
  if (marked.length >= 2 && marked.length <= 6) {
    for (const m of marked) items.push(m.replace(/^(?:[-*•]|\d{1,2}[.)]|[a-dA-D][.)])\s+/, ""));
  }
  // Inline letter enumerations: "A) x B) y C) z" (single line).
  if (!items.length) {
    const inline = text.match(/(?:^|[\s(])([a-dA-D])[.)]\s+([^;()]*?)(?=\s+[a-dA-D][.)]|[\s)]*$)/g);
    if (inline && inline.length >= 2) {
      for (const m of inline) items.push(m.replace(/^(?:^|[\s(])[a-dA-D][.)]\s+/, "").replace(/[\s)]*$/, ""));
    }
  }
  // "x, y, or z" lists (only short phrases, question asks for a choice).
  if (!items.length && /\b(which|what|how|choose|select|option|alternative|should|shall|whether|prefer)\b/i.test(text)) {
    const segments = text.replace(/\?+$/, "").split(/\?\s+/)
    // Drop a leading "<question words>: " clause ("Which color: red, ...").
    const head = segments[segments.length - 1].replace(/^[^:]{0,60}:\s+/, "")
    const parts = head
      .split(/\s*,\s*|\s+or\s+/i)
      .map((s) => s.trim().replace(/^["`(\[]+|["`)\]]+$/g, "").replace(/^[\s:;]+/, ""))
      .filter((s) => s && s.split(/\s+/).length <= 6);
    const seenParts = new Set();
    const unique = [];
    for (const p of parts) {
      const key = p.toLowerCase();
      if (seenParts.has(key)) continue;
      seenParts.add(key);
      unique.push(p);
    }
    if (unique.length >= 2 && unique.length <= 6) items.push(...unique.slice(0, 4));
  }
  const seen = new Set();
  const options = [];
  for (const raw of items) {
    const [labelPart, ...rest] = raw.split(/\s+[—–-]\s+|\s+:\s+/);
    const label = labelPart.trim().replace(/[,;]$/, "");
    if (!label || seen.has(label.toLowerCase())) continue;
    seen.add(label.toLowerCase());
    options.push({ label: label.slice(0, 60), description: rest.join(" - ").slice(0, 120) });
    if (options.length >= 4) break;
  }
  return options;
}

/** Named exports for unit tests (the default export is the plugin). */
export { normalizeInput, normalizeQuestionInput, deriveOptionsFromText, mcpAliasNames, ALIASES }

export default {
  id: "tool-name-aliases",
  async setup(ctx) {
    try {
      await ctx.tool.transform((editor) => {
        for (const [alias, canonical] of Object.entries(ALIASES)) {
          if (alias === canonical) continue
          if (editor.get(alias)) continue
          // Only register the alias when the canonical tool exists in this
          // session — a missing canonical must never become a hard error.
          if (!editor.get(canonical)) continue
          const hint = SHAPE_HINTS[canonical]
          editor.add({
            name: alias,
            description:
              `Alias for the \`${canonical}\` tool (kept for models that call it by this name). ` +
              `Arguments are forwarded automatically.` +
              (hint ? ` ${hint}` : ""),
            input: { type: "object", additionalProperties: true },
            execute: async (input, context) => {
              const tools = await ctx.tool.list()
              const target = tools.find((t) => t.id === canonical)
              if (!target) {
                throw new Error(`Tool "${alias}" maps to "${canonical}", which is not available in this session.`)
              }
              return target.execute(normalizeInput(canonical, input), context)
            },
          })
        }
        // Hallucinated mcp__server__tool names: register the observed shapes
        // as real tools (execute.before never sees unknown names, so this is
        // the only working defense). A REAL MCP tool with the same name always
        // wins — registration is skipped when the name already exists.
        for (const [alias, canonical] of mcpAliasNames()) {
          if (editor.get(alias)) continue
          if (!editor.get(canonical)) continue
          const hint = SHAPE_HINTS[canonical]
          editor.add({
            name: alias,
            description:
              `Alias for the \`${canonical}\` tool (models sometimes call it by this MCP-style name). ` +
              `Arguments are forwarded automatically.` +
              (hint ? ` ${hint}` : ""),
            input: { type: "object", additionalProperties: true },
            execute: async (input, context) => {
              const tools = await ctx.tool.list()
              const target = tools.find((t) => t.id === canonical)
              if (!target) {
                throw new Error(`Tool "${alias}" maps to "${canonical}", which is not available in this session.`)
              }
              return target.execute(normalizeInput(canonical, input), context)
            },
          })
        }
      })
      log(
        "alias tools registered:",
        Object.keys(ALIASES).join(", "),
        "| mcp-style:",
        mcpAliasNames().map(([n]) => n).join(", "),
      )
    } catch (e) {
      log("tool transform failed:", String(e))
    }

    let registration
    try {
      registration = await ctx.tool.hook("execute.before", async (event) => {
        const canonical = ALIASES[event.tool] ?? Object.fromEntries(mcpAliasNames())[event.tool]
        if (!canonical) return
        try {
          const tools = await ctx.tool.list()
          if (!tools.some((t) => t.id === canonical)) return
          event.tool = canonical
          event.input = normalizeInput(canonical, event.input)
        } catch {
          // leave the call untouched
        }
      })
    } catch {
      // hook unavailable — the registered alias tools still work
    }
    return () => registration?.dispose?.()
  },
}
