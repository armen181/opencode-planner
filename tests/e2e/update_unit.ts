/**
 * Unit harness for the versioning + update flow (R4).
 * Run via tests/e2e/test_update_unit.py (bun or node).
 */
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs"
import { execFileSync } from "node:child_process"
import { join, dirname } from "node:path"
import { tmpdir } from "node:os"
import {
  compareVersions,
  configDir,
  detectVersionStatus,
  installMarkerPath,
  pluginVersion,
  projectAgentsDir,
  projectPluginDir,
  readCachedLatest,
  refreshAgentDefinitions,
  runUpdate,
  updateCheckPath,
} from "../../src/core.ts"

const results: Array<[string, boolean]> = []
function check(name: string, ok: boolean, detail = ""): void {
  results.push([name, ok])
  console.log(`  ${ok ? "PASS" : "FAIL"}: ${name}${ok || !detail ? "" : ` — ${detail}`}`)
}

const SANDBOX = mkdtempSync(join(tmpdir(), "planner-update-unit-"))
process.env.OPENCODE_CONFIG_DIR = join(SANDBOX, "cfg")
const REPO = join(SANDBOX, "proj")

function writeJson(path: string, value: unknown): void {
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, JSON.stringify(value, null, 2))
}

function versionChecks(): void {
  check("0.4.0 < 0.4.1", compareVersions("0.4.0", "0.4.1") === -1)
  check("1.0.0 > 0.9.9", compareVersions("1.0.0", "0.9.9") === 1)
  check("v-prefixed equal", compareVersions("v1.2.3", "1.2.3") === 0)
  check("prerelease sorts before release", compareVersions("0.4.0-beta", "0.4.0") === -1)
}

function statusChecks(): void {
  const running = pluginVersion()
  writeJson(installMarkerPath(), { name: "opencode-planner", version: "0.3.0", sourceRoot: "/tmp/fake-src" })
  writeJson(join(REPO, ".opencode", "planner-plugin.json"), { plannerVersion: "0.3.0" })
  writeJson(join(projectAgentsDir(REPO), ".planner-agents.json"), { version: "0.3.0", writtenAt: "", hashes: {} })
  writeJson(join(projectPluginDir(REPO), "package.json"), { name: "x", version: "0.9.9" })
  const st = detectVersionStatus(REPO)
  check("running version from the package", running.length > 0, running)
  check("detects the project config version", st.projectConfigVersion === "0.3.0")
  check("detects the project agent marker", st.projectAgentsMarker?.version === "0.3.0")
  check("detects the project-local plugin copy", st.localPluginVersion === "0.9.9")
  check("detects the global install version", st.install?.version === "0.3.0" && st.install?.sourceRoot === "/tmp/fake-src")
  check("mismatch flagged with notes", st.mismatch && st.notes.length >= 3, JSON.stringify(st.notes))
  // A project with no markers at all is not a mismatch.
  const clean = detectVersionStatus(join(SANDBOX, "fresh"))
  check("clean project has no project-level mismatch", !clean.mismatch)
}

function refreshChecks(): void {
  // Templates resolve from this repo (packageRoot of src/core.ts).
  const templateRoot = join(dirname(new URL(import.meta.url).pathname), "..", "..")
  const template = readFileSync(join(templateRoot, "agents-v2", "planner.md"), "utf8")
  const dir = join(SANDBOX, "agents")
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, "planner.md"), template) // template-identical
  writeFileSync(join(dir, "worker.md"), "USER EDITED — do not touch\n") // user content, no marker

  const first = refreshAgentDefinitions(dir)
  check("missing agent created", first.updated.includes("brainstormer") || existsSync(join(dir, "brainstormer.md")))
  check("template-identical file refreshed", first.updated.includes("planner"))
  check("user-edited file kept", first.kept.includes("worker") && readFileSync(join(dir, "worker.md"), "utf8").startsWith("USER"))

  // After the refresh the marker knows planner's hash; a later user edit must
  // then be kept, not overwritten — ALSO on every further refresh (a kept file
  // must never be recorded as plugin-written).
  writeFileSync(join(dir, "planner.md"), template + "\nuser tweak\n")
  const second = refreshAgentDefinitions(dir)
  check("edit after a recorded refresh is kept", second.kept.includes("planner"))
  check("untouched-after-refresh file is updated again", second.updated.includes("tester") || existsSync(join(dir, "tester.md")))
  const third = refreshAgentDefinitions(dir)
  check("repeat refresh still keeps the user edit", third.kept.includes("planner") && third.kept.includes("worker"))
  check("repeat refresh leaves the edited content intact",
        readFileSync(join(dir, "planner.md"), "utf8").includes("user tweak")
        && readFileSync(join(dir, "worker.md"), "utf8").startsWith("USER"))

  // force: legacy/edited files are backed up, then overwritten with the template
  const forced = refreshAgentDefinitions(dir, { force: true })
  check("force refresh overwrites edited files", forced.updated.includes("planner") && forced.updated.includes("worker"))
  check("force refresh lists the backups", forced.backedUp.includes("planner") && forced.backedUp.includes("worker"))
  check("force refresh backs the content up first",
        readFileSync(join(dir, "planner.md.bak"), "utf8").includes("user tweak")
        && readFileSync(join(dir, "worker.md.bak"), "utf8").startsWith("USER"))
  check("force refresh replaces the content with the template",
        readFileSync(join(dir, "planner.md"), "utf8") === template)
}

function cacheChecks(): void {
  mkdirSync(configDir(), { recursive: true })
  writeJson(updateCheckPath(), { latest: "9.9.9", checkedAt: Date.now() })
  check("fresh cache is used", readCachedLatest()?.latest === "9.9.9")
  writeJson(updateCheckPath(), { latest: "9.9.9", checkedAt: Date.now() - 48 * 3600 * 1000 })
  check("stale cache is ignored", readCachedLatest() === undefined)
  check("stale cache is returned when forced", readCachedLatest(true)?.latest === "9.9.9")
}

async function updateChecks(): Promise<void> {
  // A real (tiny) git repo with a stub installer + a clone of it (so `git
  // pull --ff-only` succeeds against the origin).
  const src = join(SANDBOX, "fake-src")
  mkdirSync(src, { recursive: true })
  writeFileSync(
    join(src, "install.sh"),
    '#!/usr/bin/env bash\necho "stub install ran"\necho "ran" > "$(dirname "$0")/ran.txt"\n',
  )
  execFileSync("git", ["-C", src, "init", "-q"])
  execFileSync("git", ["-C", src, "add", "-A"])
  execFileSync("git", ["-C", src, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"])
  const clone = join(SANDBOX, "clone")
  execFileSync("git", ["clone", "-q", src, clone])
  const logPath = join(SANDBOX, "update.log")
  const res = await runUpdate(clone, logPath)
  check("clone update succeeds (pull + install)", res.ok, res.error ?? "")
  check("stub installer executed from the clone", existsSync(join(clone, "ran.txt")))
  const log = existsSync(logPath) ? readFileSync(logPath, "utf8") : ""
  check("update log has the full commands + output",
        log.includes("git -C") && log.includes("install.sh") && log.includes("stub install ran"))

  // A repo WITHOUT a remote: the pull fails and the stale tree must NOT be
  // reinstalled.
  const src2 = join(SANDBOX, "no-remote")
  mkdirSync(src2, { recursive: true })
  writeFileSync(
    join(src2, "install.sh"),
    '#!/usr/bin/env bash\necho "ran" > "$(dirname "$0")/ran.txt"\n',
  )
  execFileSync("git", ["-C", src2, "init", "-q"])
  execFileSync("git", ["-C", src2, "add", "-A"])
  execFileSync("git", ["-C", src2, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"])
  const noRemote = await runUpdate(src2, join(SANDBOX, "update-noremote.log"))
  check("failed pull aborts the update", !noRemote.ok && Boolean(noRemote.error?.includes("pull")), noRemote.error)
  check("no reinstall from a stale tree", !existsSync(join(src2, "ran.txt")))
  check("abort still writes the log", existsSync(join(SANDBOX, "update-noremote.log")))

  // Not a git repo → clear abort, installer NOT run.
  const plain = join(SANDBOX, "plain-src")
  mkdirSync(plain, { recursive: true })
  writeFileSync(join(plain, "install.sh"), "#!/usr/bin/env bash\ntouch \"$(dirname \"$0\")/ran.txt\"\n")
  const aborted = await runUpdate(plain, join(SANDBOX, "update2.log"))
  check("non-repo source aborts with a clear error", !aborted.ok && Boolean(aborted.error?.includes("git clone")), aborted.error)
  check("installer not executed on abort", !existsSync(join(plain, "ran.txt")))
  check("non-repo abort still writes the log", existsSync(join(SANDBOX, "update2.log")))

  const missing = await runUpdate(join(SANDBOX, "nope"), join(SANDBOX, "update3.log"))
  check("missing source aborts with a clear error", !missing.ok && Boolean(missing.error?.includes("install.sh")))
  check("missing-source abort still writes the log", existsSync(join(SANDBOX, "update3.log")))
}

async function main(): Promise<void> {
  console.log("update-flow unit harness")
  versionChecks()
  statusChecks()
  refreshChecks()
  cacheChecks()
  await updateChecks()
  const failed = results.filter(([, ok]) => !ok).length
  console.log(`${results.length - failed}/${results.length} checks passed`)
  try {
    rmSync(SANDBOX, { recursive: true, force: true })
  } catch {
    // best-effort
  }
  process.exit(failed ? 1 : 0)
}

void main()
