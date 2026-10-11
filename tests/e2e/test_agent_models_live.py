#!/usr/bin/env python3
"""Live per-agent model pinning E2E — real model, real OpenCode 2 server.

Verifies that the models selected in /planner-config are the models the
subagents actually RUN with (not just what the agent registry shows):

  Scenario 1 — project agent files present: a planner request runs the
  pipeline and every child session (brainstormer/worker/tester) records its
  OWN pinned model in the session DB, not the planner's session model.
  Scenario 2 (regression) — the project's .opencode/agents directory is
  deleted while the server runs and the config is touched to force a reload:
  the pins must STILL apply (the old code gated pinning on the project file
  and silently fell back to the main model).

This suite makes real model calls and takes minutes — it is NOT part of
run_all.sh. Provider block is read from the user's ~/.config/opencode.

Run: python3 -u tests/e2e/test_agent_models_live.py
Env: LT_PROVIDER (goproxy), PLANNER_V2_BIN, LT_MODELS_TIMEOUT (default 420).
"""
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

SCRATCH = "/tmp/planner-agent-models-test"
HOME_DIR = "/tmp/planner-agent-models-home"
LOG_PATH = "/tmp/planner-agent-models-server.log"

PROVIDER = os.environ.get("LT_PROVIDER", "goproxy")
MODELS = {
    "planner": "deepseek-v4.1-flash",
    "brainstormer": "glm-5.3-flash",
    "worker": "deepseek-v4-flash",
    "tester": "mimo-v2.5",
}

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    suffix = "" if ok else f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}: {name}{suffix}")


def info(name, value):
    print(f"  INFO: {name} = {value}")


def sh(cmd, cwd=SCRATCH, env=None):
    return subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, env=env)


def find_binary():
    explicit = os.environ.get("PLANNER_V2_BIN")
    candidates = [explicit] if explicit else []
    candidates += [shutil.which("opencode2") or "", shutil.which("opencode") or ""]
    for cand in candidates:
        if not cand or not os.path.exists(cand):
            continue
        try:
            out = subprocess.run([cand, "--version"], capture_output=True, text=True, timeout=30)
        except Exception:
            continue
        version = (out.stdout + out.stderr).strip()
        if "v2." in version or version.startswith("2.") or "-beta-" in version:
            return cand
    return None


def server_env():
    env = os.environ.copy()
    env["HOME"] = HOME_DIR
    env["XDG_CONFIG_HOME"] = os.path.join(HOME_DIR, ".config")
    env["XDG_DATA_HOME"] = os.path.join(HOME_DIR, ".local", "share")
    env["XDG_CACHE_HOME"] = os.path.join(HOME_DIR, ".cache")
    env["PLANNER_NO_UPDATE_CHECK"] = "1"
    return env


def real_provider_block(provider):
    for name in ("opencode.json", "config.json"):
        path = os.path.expanduser(f"~/.config/opencode/{name}")
        if not os.path.exists(path):
            continue
        try:
            data = json.load(open(path))
        except Exception:
            continue
        block = (data.get("provider") or {}).get(provider)
        if block:
            return block
    return None


def write_config(max_cycles=3):
    cfg = {
        "models": {
            "planner": "",
            "brainstormer": f"{PROVIDER}/{MODELS['brainstormer']}",
            "worker": f"{PROVIDER}/{MODELS['worker']}",
            "tester": f"{PROVIDER}/{MODELS['tester']}",
        },
        "variants": {},
        "maxCycles": max_cycles,
        "maxParallelSubagents": 1,
        "enforceDelegation": True,
        "vision": {"enabled": False, "model": "", "mode": "agent"},
    }
    os.makedirs(os.path.join(SCRATCH, ".opencode"), exist_ok=True)
    with open(os.path.join(SCRATCH, ".opencode", "planner-plugin.json"), "w") as f:
        json.dump(cfg, f, indent=2)


def setup_scratch():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    pkg = os.path.join(SCRATCH, ".opencode", "plugins", "opencode-planner")
    os.makedirs(pkg)
    shutil.copytree(os.path.join(ROOT, "src"), os.path.join(pkg, "src"))
    shutil.copytree(os.path.join(ROOT, "agents-v2"), os.path.join(pkg, "agents-v2"))
    for f in ("index.ts", "tui.ts", "package.json"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(pkg, f))
    write_config()
    # Global agents in the private HOME (so agents exist without project files
    # — exactly the user's setup where pins must still apply).
    agents_dir = os.path.join(HOME_DIR, ".config", "opencode", "agents")
    os.makedirs(agents_dir, exist_ok=True)
    for name in ("planner", "brainstormer", "worker", "tester", "vision"):
        shutil.copy(os.path.join(ROOT, "agents-v2", f"{name}.md"), os.path.join(agents_dir, f"{name}.md"))
    sh("git init -q -b main")
    sh("git config user.email t@t.com")
    sh("git config user.name t")
    with open(os.path.join(SCRATCH, "README.md"), "w") as f:
        f.write("# planner agent-model pinning test\n")
    sh("git add -A && git commit -qm init")


def write_home_config():
    cfg_dir = os.path.join(HOME_DIR, ".config", "opencode")
    os.makedirs(cfg_dir, exist_ok=True)
    block = real_provider_block(PROVIDER)
    if not block:
        return False
    block = json.loads(json.dumps(block))
    models = block.setdefault("models", {})
    for mid in MODELS.values():
        models.setdefault(mid, {"name": mid})
    with open(os.path.join(cfg_dir, "opencode.json"), "w") as f:
        json.dump({
            "$schema": "https://opencode.ai/config.json",
            "model": f"{PROVIDER}/{MODELS['planner']}",
            "provider": {PROVIDER: block},
        }, f, indent=2)
    return True


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class V2:
    def __init__(self, binary, password, port):
        self.binary = binary
        self.password = password
        self.port = port

    def api(self, op, params=None, body=None, timeout=120):
        cmd = [self.binary, "api", op, "--server", f"http://127.0.0.1:{self.port}"]
        for k, v in (params or {}).items():
            cmd += ["--param", f"{k}={v}"]
        if body is not None:
            cmd += ["-d", json.dumps(body)]
        env = server_env()
        env["OPENCODE_PASSWORD"] = self.password
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=SCRATCH)
        text = out.stdout.strip()
        try:
            return json.loads(text) if text else None
        except json.JSONDecodeError:
            return None

    def create(self, title):
        return ((self.api("session.create", body={"title": title}) or {}).get("data") or {}).get("id")

    def switch_planner(self, sid):
        self.api("session.switchAgent", {"sessionID": sid}, body={"agent": "planner"})
        return ((self.api("session.get", {"sessionID": sid}) or {}).get("data") or {}).get("agent")

    def prompt_async(self, sid, text):
        cmd = [self.binary, "api", "session.prompt", "--server", f"http://127.0.0.1:{self.port}",
               "--param", f"sessionID={sid}", "-d", json.dumps({"text": text})]
        env = server_env()
        env["OPENCODE_PASSWORD"] = self.password
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=SCRATCH)


def child_models(sid):
    db = sqlite3.connect(f"file:{os.path.join(HOME_DIR, '.local', 'share', 'opencode', 'opencode.db')}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    out = []
    for r in db.execute("SELECT agent, model FROM session_v2 WHERE parent_id=?", (sid,)):
        try:
            m = json.loads(r["model"]) if r["model"] else None
        except Exception:
            m = None
        out.append((r["agent"], m))
    return out


PROMPT = ("Create a file named hello-{n}.txt in the project root containing exactly the text: hello. "
          "Then verify the file exists with the right content.")


def run_pipeline(api, label, n, timeout):
    sid = api.create(f"models-{label}")
    agent = api.switch_planner(sid)
    api.prompt_async(sid, PROMPT.format(n=n))
    want = {"brainstormer", "worker", "tester"}
    deadline = time.time() + timeout
    seen = {}
    while time.time() < deadline:
        time.sleep(8)
        kids = child_models(sid)
        seen = dict(kids)
        if want.issubset(seen.keys()):
            break
        if os.path.exists(os.path.join(SCRATCH, f"hello-{n}.txt")) and "tester" in seen:
            break
    time.sleep(3)
    return sid, agent, dict(child_models(sid))


def assert_pins(label, kids):
    ok = True
    for agent, expected in (("brainstormer", MODELS["brainstormer"]),
                            ("worker", MODELS["worker"]),
                            ("tester", MODELS["tester"])):
        if agent not in kids:
            check(f"{label}: {agent} child session exists", False, f"children={sorted(kids)}")
            ok = False
            continue
        m = kids[agent] or {}
        got = f"{m.get('providerID')}/{m.get('id')}"
        check(f"{label}: {agent} ran its pinned model", got == f"{PROVIDER}/{expected}", f"got={got}")
        ok = ok and got == f"{PROVIDER}/{expected}"
    return ok


def main():
    binary = find_binary()
    if not binary:
        print("[SKIP] No OpenCode V2 binary found (set PLANNER_V2_BIN).")
        return 0
    os.makedirs(HOME_DIR, exist_ok=True)
    setup_scratch()
    if not write_home_config():
        print(f"[SKIP] provider {PROVIDER} not found in ~/.config/opencode config")
        return 0

    port = free_port()
    with open(LOG_PATH, "w") as logf:
        server = subprocess.Popen(
            [binary, "serve", "--port", str(port), "--print-logs"],
            cwd=SCRATCH, stdout=logf, stderr=logf, env=server_env(), preexec_fn=os.setsid,
        )
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
        api = V2(binary, password, port)
        api.api("plugin.list")
        time.sleep(3)

        timeout = int(os.environ.get("LT_MODELS_TIMEOUT", "420"))
        print("\n[Scenario 1] project agent files present")
        sid1, agent1, kids1 = run_pipeline(api, "s1", 1, timeout)
        info("s1.session_agent", agent1)
        info("s1.child_models", {k: v for k, v in kids1.items()})
        check("scenario 1 ran on the planner agent", agent1 == "planner", str(agent1))
        assert_pins("scenario 1", kids1)

        print("\n[Scenario 2] project agents deleted + config reload (regression)")
        shutil.rmtree(os.path.join(SCRATCH, ".opencode", "agents"), ignore_errors=True)
        check("project agents directory removed", not os.path.isdir(os.path.join(SCRATCH, ".opencode", "agents")))
        write_config(max_cycles=4)  # mtime change → watcher reloads + replays the transform
        time.sleep(5)
        sid2, agent2, kids2 = run_pipeline(api, "s2", 2, timeout)
        info("s2.session_agent", agent2)
        info("s2.child_models", {k: v for k, v in kids2.items()})
        check("scenario 2 ran on the planner agent", agent2 == "planner", str(agent2))
        assert_pins("scenario 2", kids2)
        check("no child inherited the planner's model in scenario 2",
              all((m or {}).get("id") != MODELS["planner"] for m in kids2.values()),
              str(kids2))

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
