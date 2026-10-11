#!/usr/bin/env python3
"""Live brainstormer spec-write E2E (R7) — real model, real OpenCode 2 server.

Regression for: "the brainstormer generated the requirements md but never wrote
it to disk". A bounded multi-file request is sent to the planner; the
brainstormer must WRITE the spec file under docs/superpowers/specs/, the file
must exist and contain Requirements + Acceptance criteria, and the pipeline
(brainstormer → worker → tester) must run.

Run: python3 -u tests/e2e/test_spec_write_live.py   (real model calls)
Env: LT_PROVIDER (goproxy), PLANNER_V2_BIN, LT_SPEC_TIMEOUT (default 420).
"""
import glob
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRATCH = "/tmp/planner-spec-write-test"
HOME_DIR = "/tmp/planner-spec-write-home"
LOG_PATH = "/tmp/planner-spec-write-server.log"
PROVIDER = os.environ.get("LT_PROVIDER", "goproxy")
MODELS = {"planner": "deepseek-v4.1-flash", "brainstormer": "glm-5.3-flash",
          "worker": "glm-5.3-flash", "tester": "glm-5.3-flash"}

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'}: {name}{'' if ok or not detail else f' — {detail}'}")


def sh(cmd, cwd=SCRATCH):
    return subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True)


def find_binary():
    explicit = os.environ.get("PLANNER_V2_BIN")
    candidates = ([explicit] if explicit else []) + [shutil.which("opencode2") or "", shutil.which("opencode") or ""]
    for cand in candidates:
        if cand and os.path.exists(cand):
            out = subprocess.run([cand, "--version"], capture_output=True, text=True, timeout=30)
            v = (out.stdout + out.stderr).strip()
            if "v2." in v or v.startswith("2.") or "-beta-" in v:
                return cand
    return None


def server_env():
    env = os.environ.copy()
    env.update({
        "HOME": HOME_DIR,
        "XDG_CONFIG_HOME": os.path.join(HOME_DIR, ".config"),
        "XDG_DATA_HOME": os.path.join(HOME_DIR, ".local", "share"),
        "XDG_CACHE_HOME": os.path.join(HOME_DIR, ".cache"),
        "PLANNER_NO_UPDATE_CHECK": "1",
        "PLANNER_DEBUG": "1",
        "PLANNER_DEBUG_FILE": "/tmp/planner-spec-write-debug.log",
    })
    return env


def provider_block(provider):
    for name in ("opencode.json", "config.json"):
        p = os.path.expanduser(f"~/.config/opencode/{name}")
        if os.path.exists(p):
            try:
                d = json.load(open(p))
                b = (d.get("provider") or {}).get(provider)
                if b:
                    return b
            except Exception:
                pass
    return None


def setup():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    pkg = os.path.join(SCRATCH, ".opencode", "plugins", "opencode-planner")
    os.makedirs(pkg)
    shutil.copytree(os.path.join(ROOT, "src"), os.path.join(pkg, "src"))
    shutil.copytree(os.path.join(ROOT, "agents-v2"), os.path.join(pkg, "agents-v2"))
    for f in ("index.ts", "tui.ts", "package.json"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(pkg, f))
    with open(os.path.join(SCRATCH, ".opencode", "planner-plugin.json"), "w") as f:
        json.dump({"models": {"planner": "", "brainstormer": f"{PROVIDER}/{MODELS['brainstormer']}",
                              "worker": f"{PROVIDER}/{MODELS['worker']}", "tester": f"{PROVIDER}/{MODELS['tester']}"},
                   "variants": {}, "maxCycles": 2, "maxParallelSubagents": 1, "enforceDelegation": True,
                   "vision": {"enabled": False, "model": "", "mode": "agent"}}, f, indent=2)
    block = provider_block(PROVIDER)
    if not block:
        return False
    block = json.loads(json.dumps(block))
    for mid in MODELS.values():
        block.setdefault("models", {}).setdefault(mid, {"name": mid})
    cfg_dir = os.path.join(HOME_DIR, ".config", "opencode")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, "opencode.json"), "w") as f:
        json.dump({"$schema": "https://opencode.ai/config.json",
                   "model": f"{PROVIDER}/{MODELS['planner']}",
                   "provider": {PROVIDER: block}}, f, indent=2)
    sh("git init -q -b main")
    sh("git config user.email t@t.com")
    sh("git config user.name t")
    open(os.path.join(SCRATCH, "README.md"), "w").write("# spec write test\n")
    sh("git add -A && git commit -qm init")
    return True


def main():
    binary = find_binary()
    if not binary:
        print("[SKIP] No OpenCode V2 binary found (set PLANNER_V2_BIN).")
        return 0
    os.makedirs(HOME_DIR, exist_ok=True)
    if not setup():
        print(f"[SKIP] provider {PROVIDER} not found in ~/.config/opencode config")
        return 0
    if os.path.exists("/tmp/planner-spec-write-debug.log"):
        os.remove("/tmp/planner-spec-write-debug.log")

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with open(LOG_PATH, "w") as logf:
        server = subprocess.Popen([binary, "serve", "--port", str(port), "--print-logs"],
                                  cwd=SCRATCH, stdout=logf, stderr=logf, env=server_env(), preexec_fn=os.setsid)
    try:
        password = ""
        deadline = time.time() + 60
        while time.time() < deadline and not password:
            for line in open(LOG_PATH, errors="ignore"):
                if "server password" in line:
                    password = line.strip().split()[-1]
            if not password:
                time.sleep(0.5)
        check("server booted", bool(password))
        if not password:
            return 1
        env = server_env()
        env["OPENCODE_PASSWORD"] = password
        base = ["--server", f"http://127.0.0.1:{port}"]

        def api(op, params=None, body=None):
            cmd = [binary, "api", op] + base
            for k, v in (params or {}).items():
                cmd += ["--param", f"{k}={v}"]
            if body is not None:
                cmd += ["-d", json.dumps(body)]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env, cwd=SCRATCH)
            try:
                return json.loads(out.stdout.strip()) if out.stdout.strip() else None
            except json.JSONDecodeError:
                return None

        api("plugin.list")
        time.sleep(3)
        sid = ((api("session.create", body={"title": "spec-write"}) or {}).get("data") or {}).get("id")
        api("session.switchAgent", {"sessionID": sid}, body={"agent": "planner"})
        prompt = ("Create a small Python CLI package `wordcount/`: `wordcount/core.py` with "
                  "`count_words(text) -> dict`, `wordcount/__init__.py`, `wordcount/__main__.py` so "
                  "`python -m wordcount <file>` prints counts, a `tests/test_core.py` pytest file, and "
                  "README usage. It is a multi-file change — follow the planner workflow.")
        subprocess.Popen([binary, "api", "session.prompt"] + base + ["--param", f"sessionID={sid}",
                         "-d", json.dumps({"text": prompt})], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=env, cwd=SCRATCH)

        timeout = int(os.environ.get("LT_SPEC_TIMEOUT", "420"))
        deadline = time.time() + timeout
        spec = None
        while time.time() < deadline:
            time.sleep(10)
            found = glob.glob(os.path.join(SCRATCH, "docs", "superpowers", "specs", "*.md"))
            if found:
                spec = found[0]
                break
        check("brainstormer wrote the spec file to disk", bool(spec), "no file under docs/superpowers/specs/")
        if spec:
            text = open(spec).read()
            check("spec file is non-empty", len(text.strip()) > 100, f"{len(text)} chars")
            low = text.lower()
            check("spec has Requirements + Acceptance criteria",
                  "requirement" in low and "acceptance" in low, spec)
            print(f"  INFO: spec={spec} ({len(text)} chars)")

        def children():
            checker = subprocess.run(["python3", "-c", f"""
import sqlite3, json
db = sqlite3.connect('file:{os.path.join(HOME_DIR, '.local', 'share', 'opencode', 'opencode.db')}?mode=ro', uri=True)
db.row_factory = sqlite3.Row
cards = {{}}
for r in db.execute('SELECT agent, model FROM session_v2 WHERE parent_id=?', ('{sid}',)):
    cards[r['agent']] = r['model']
print(json.dumps(cards))
"""], capture_output=True, text=True)
            try:
                return json.loads(checker.stdout.strip() or "{}")
            except json.JSONDecodeError:
                return {}

        # The spec lands during the brainstormer phase — keep waiting for the
        # rest of the pipeline (worker, then tester) before judging it.
        kids = children()
        while time.time() < deadline and not all(a in kids for a in ("brainstormer", "worker", "tester")):
            time.sleep(10)
            kids = children()
        print(f"  INFO: children={kids}")
        check("pipeline ran (brainstormer + worker + tester)",
              all(a in kids for a in ("brainstormer", "worker", "tester")), str(sorted(kids)))

        passed = sum(1 for _, ok in RESULTS if ok)
        print(f"\n{passed}/{len(RESULTS)} checks passed")
        return 0 if passed == len(RESULTS) else 1
    finally:
        try:
            os.killpg(os.getpgid(server.pid), signal.SIGTERM)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
