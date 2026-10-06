#!/usr/bin/env python3
"""Live model-switch test: changing a subagent's model takes effect with NO
opencode restart, proven with the GoProxy dashboard request log.

Steps:
  1. Boot one `opencode serve` process in a scratch project (private HOME,
     goproxy provider copied from the real config).
  2. Session A: planner → worker → tester with tester = glm-5.3-flash. The
     proxy dashboard must record a glm-5.3-flash request in the window.
  3. WITHOUT restarting opencode, rewrite `.opencode/planner-plugin.json` to
     tester = mimo-v2.5 and wait for the plugin watcher to apply it to the
     agent registry (same server PID).
  4. Session B: same flow; the dashboard must record mimo-v2.5 in the second
     window (and the old tester model must not be the one used).

Requires the GoProxy dashboard credentials for the proxy assertions:
  GOPROXY_UI (default http://localhost:4881), GOPROXY_UI_USER,
  GOPROXY_UI_PASSWORD.  Without a password the run still validates the live
model change, but skips the dashboard assertions.

Run: python3 tests/e2e/test_model_switch_live.py
"""
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
sys.path.insert(0, HERE)

import proxy_log  # noqa: E402

SCRATCH = "/tmp/planner-switch-test"
HOME_DIR = "/tmp/planner-switch-test-home"
PROVIDER = os.environ.get("SWITCH_PROVIDER", "goproxy")
MODEL_A = os.environ.get("SWITCH_MODEL_A", "glm-5.3-flash")
MODEL_B = os.environ.get("SWITCH_MODEL_B", "mimo-v2.5")
WORKER_MODEL = os.environ.get("SWITCH_WORKER_MODEL", "deepseek-v4.1-flash")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    suffix = "" if ok else f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}: {name}{suffix}")


def sh(cmd, cwd=SCRATCH, env=None):
    return subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, env=env)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


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


def write_config(tester_model):
    with open(os.path.join(SCRATCH, ".opencode", "planner-plugin.json"), "w") as f:
        json.dump({
            "models": {
                "planner": "",
                "brainstormer": "",
                "worker": f"{PROVIDER}/{WORKER_MODEL}",
                "tester": f"{PROVIDER}/{tester_model}",
            },
            "variants": {},
            "maxCycles": 2,
            "maxParallelSubagents": 1,
            "vision": {"enabled": False, "model": "", "mode": "agent"},
        }, f, indent=2)


def setup():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    os.makedirs(os.path.join(HOME_DIR, ".config", "opencode"), exist_ok=True)
    pkg = os.path.join(SCRATCH, ".opencode", "plugins", "opencode-planner")
    os.makedirs(pkg)
    shutil.copytree(os.path.join(ROOT, "src"), os.path.join(pkg, "src"))
    shutil.copytree(os.path.join(ROOT, "agents-v2"), os.path.join(pkg, "agents-v2"))
    for f in ("index.ts", "tui.ts", "package.json"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(pkg, f))
    write_config(MODEL_A)
    block = real_provider_block(PROVIDER)
    if not block:
        return False
    block = json.loads(json.dumps(block))
    models = block.setdefault("models", {})
    for mid in (MODEL_A, MODEL_B, WORKER_MODEL, "deepseek-v4.1-flash"):
        if mid and mid not in models:
            models[mid] = {"name": mid}
    with open(os.path.join(HOME_DIR, ".config", "opencode", "opencode.json"), "w") as f:
        json.dump({
            "$schema": "https://opencode.ai/config.json",
            "model": f"{PROVIDER}/{WORKER_MODEL}",
            "provider": {PROVIDER: block},
        }, f, indent=2)
    sh("git init -q -b main")
    sh("git config user.email t@t.com")
    sh("git config user.name t")
    with open(os.path.join(SCRATCH, "README.md"), "w") as f:
        f.write("# planner model switch test\n")
    sh("git add -A && git commit -qm init")
    return True


class V2:
    def __init__(self, binary, password, port):
        self.binary = binary
        self.password = password
        self.port = port

    def api(self, op, params=None, body=None, timeout=90):
        cmd = [self.binary, "api", op, "--server", f"http://127.0.0.1:{self.port}"]
        for k, v in (params or {}).items():
            cmd += ["--param", f"{k}={v}"]
        if body is not None:
            cmd += ["-d", json.dumps(body)]
        env = server_env()
        env["OPENCODE_PASSWORD"] = self.password
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=SCRATCH)
        try:
            return json.loads(out.stdout.strip()) if out.stdout.strip() else None
        except json.JSONDecodeError:
            return None

    def tester_model_id(self):
        agents = (self.api("agent.list") or {}).get("data", [])
        for a in agents:
            if a.get("id") == "tester":
                return (a.get("model") or {}).get("id")
        return None

    def poll_tester_model(self, want, timeout=40):
        deadline = time.time() + timeout
        seen = None
        while time.time() < deadline:
            seen = self.tester_model_id()
            if seen == want:
                return seen
            time.sleep(1.5)
        return seen

    def prompt(self, sid, text, agent="planner"):
        cmd = [self.binary, "api", "session.prompt", "--server", f"http://127.0.0.1:{self.port}",
               "--param", f"sessionID={sid}", "-d", json.dumps({"text": text, "agent": agent})]
        env = server_env()
        env["OPENCODE_PASSWORD"] = self.password
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=SCRATCH)

    def context(self, sid):
        res = self.api("session.context", {"sessionID": sid}) or {}
        msgs = res.get("data")
        return msgs if isinstance(msgs, list) else []

    def wait_done(self, sid, marker, timeout=900):
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            time.sleep(10)
            for m in reversed(self.context(sid)):
                if m.get("type") == "assistant":
                    txt = "\n".join(c.get("text", "") for c in (m.get("content") or []) if c.get("type") == "text")
                    if txt:
                        last = txt
                        break
            if marker in last.upper():
                return True, last
        return False, last


def task_text(label):
    return (
        f"Scripted model-switch test ({label}). This is TRIVIAL: skip the brainstormer. "
        f"Do NOT ask questions. Delegate to the worker subagent: append one line `run {label}` to "
        f"`sw_{label}.txt`. Then delegate to the tester subagent to verify the line exists; the tester "
        f"replies with a STATUS line. Then summarize and end with the exact text: SWITCH {label} DONE"
    )


def main():
    binary = find_binary()
    if not binary:
        print("[SKIP] No OpenCode V2 binary found (set PLANNER_V2_BIN).")
        return 0
    if not setup():
        print(f"[SKIP] provider {PROVIDER} not found in ~/.config/opencode config")
        return 0

    proxy = proxy_log.available()
    if not proxy:
        print("[WARN] GOPROXY_UI_PASSWORD not set — dashboard assertions will be skipped.")

    port = free_port()
    logf = open("/tmp/planner-switch-test-server.log", "w")
    server = subprocess.Popen(
        [binary, "serve", "--port", str(port), "--print-logs"],
        cwd=SCRATCH, stdout=logf, stderr=logf, env=server_env(), preexec_fn=os.setsid,
    )
    server_pid = server.pid
    try:
        password = ""
        deadline = time.time() + 60
        while time.time() < deadline and not password:
            for line in open("/tmp/planner-switch-test-server.log"):
                if "server password" in line:
                    password = line.strip().split()[-1]
            if not password:
                time.sleep(0.5)
        check("server booted", bool(password))
        if not password:
            return 1
        api = V2(binary, password, port)

        pinned_a = api.poll_tester_model(MODEL_A, timeout=45)
        check(f"tester pinned at boot ({MODEL_A})", pinned_a == MODEL_A, f"registry shows {pinned_a!r}")

        # ---------------------------------------------------------- session A
        print(f"[switch] session A with tester={MODEL_A}…")
        t_a = proxy_log.now()
        sid_a = ((api.api("session.create", body={"title": "switch-A"}) or {}).get("data") or {}).get("id")
        api.prompt(sid_a, task_text("a"))
        done_a, last_a = api.wait_done(sid_a, "SWITCH A DONE")
        check("session A finished", done_a, last_a[-200:])

        # ------------------------------------------------- no restart, switch
        print(f"[switch] changing tester {MODEL_A} -> {MODEL_B} (no restart)…")
        write_config(MODEL_B)
        applied = api.poll_tester_model(MODEL_B, timeout=45)
        check(f"tester model switched to {MODEL_B} without restart",
              applied == MODEL_B, f"registry shows {applied!r}")
        check("the same server process is still running",
              server.poll() is None and server.pid == server_pid)

        # ---------------------------------------------------------- session B
        print(f"[switch] session B with tester={MODEL_B}…")
        t_b = proxy_log.now()
        sid_b = ((api.api("session.create", body={"title": "switch-B"}) or {}).get("data") or {}).get("id")
        api.prompt(sid_b, task_text("b"))
        done_b, last_b = api.wait_done(sid_b, "SWITCH B DONE")
        check("session B finished", done_b, last_b[-200:])

        # ------------------------------------------------- proxy log evidence
        if proxy:
            ok_a, models_a = proxy_log.wait_for_model(MODEL_A, t_a, timeout=120)
            ok_b, models_b = proxy_log.wait_for_model(MODEL_B, t_b, timeout=180)
            check(f"dashboard saw {MODEL_A} during session A", ok_a, str(models_a[-6:]))
            check(f"dashboard saw {MODEL_B} during session B (after the live switch)", ok_b, str(models_b[-6:]))
            check("old tester model not used after the switch",
                  MODEL_A not in models_b, str(models_b[-6:]))
            print(f"[switch] models in window A: {sorted(set(models_a))}")
            print(f"[switch] models in window B: {sorted(set(models_b))}")
    finally:
        try:
            os.killpg(os.getpgid(server.pid), signal.SIGTERM)
        except Exception:
            pass
        logf.close()

    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    if passed == len(RESULTS):
        shutil.rmtree(SCRATCH, ignore_errors=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
