#!/usr/bin/env python3
"""Live concurrency-gate E2E (R1) — real model, real OpenCode 2 server.

Boots `opencode serve` on a scratch project with the plugin (PLANNER_DEBUG=1)
and fires the probe in TWO planner sessions at the same time, so two subagent
calls are demanded simultaneously:

  Phase 1 (maxParallelSubagents=1): the debug log must show no two subagent
  tasks in flight at once; the second call waits for the first (waitedMs > 0).
  Phase 2 (raised live to 2 via the config watcher): the same probe must run
  the two tasks simultaneously (inFlight=2), proving the cap follows the
  config without a restart.

This suite makes real model calls and takes minutes — it is NOT part of
run_all.sh. The provider block is read from the user's ~/.config/opencode.

Run: python3 tests/e2e/test_subagent_limit_live.py
Env: LT_PROVIDER (goproxy), LT_MODEL (planner), LT_WORKER_MODEL, PLANNER_V2_BIN,
     LT_PROBE_TIMEOUT (seconds per phase, default 420).
"""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

SCRATCH = "/tmp/planner-limit-test"
HOME_DIR = "/tmp/planner-limit-test-home"
LOG_PATH = "/tmp/planner-limit-test-server.log"
DEBUG_PATH = "/tmp/planner-limit-test-debug.log"

RESULTS = []
INFO = {}


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    suffix = "" if ok else f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}: {name}{suffix}")


def info(name, value):
    INFO[name] = value
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
    env["PLANNER_DEBUG"] = "1"
    env["PLANNER_DEBUG_FILE"] = DEBUG_PATH
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


def write_config(max_parallel, provider, models):
    cfg = {
        "models": {
            "planner": "",
            "brainstormer": f"{provider}/{models['planner']}",
            "worker": f"{provider}/{models['worker']}",
            "tester": f"{provider}/{models['tester']}",
        },
        "variants": {},
        "maxCycles": 1,
        "maxParallelSubagents": max_parallel,
        "vision": {"enabled": False, "model": "", "mode": "agent"},
    }
    os.makedirs(os.path.join(SCRATCH, ".opencode"), exist_ok=True)
    with open(os.path.join(SCRATCH, ".opencode", "planner-plugin.json"), "w") as f:
        json.dump(cfg, f, indent=2)


def setup_scratch(provider, models):
    shutil.rmtree(SCRATCH, ignore_errors=True)
    pkg = os.path.join(SCRATCH, ".opencode", "plugins", "opencode-planner")
    os.makedirs(pkg)
    shutil.copytree(os.path.join(ROOT, "src"), os.path.join(pkg, "src"))
    shutil.copytree(os.path.join(ROOT, "agents-v2"), os.path.join(pkg, "agents-v2"))
    for f in ("index.ts", "tui.ts", "package.json"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(pkg, f))
    write_config(1, provider, models)
    sh("git init -q -b main")
    sh("git config user.email t@t.com")
    sh("git config user.name t")
    with open(os.path.join(SCRATCH, "README.md"), "w") as f:
        f.write("# planner concurrency gate live test\n")
    sh("git add -A && git commit -qm init")


def write_home_config(provider, model, model_ids):
    cfg_dir = os.path.join(HOME_DIR, ".config", "opencode")
    os.makedirs(cfg_dir, exist_ok=True)
    block = real_provider_block(provider)
    if not block:
        return False
    block = json.loads(json.dumps(block))
    models = block.setdefault("models", {})
    for mid in model_ids:
        if mid and mid not in models:
            models[mid] = {"name": mid}
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{provider}/{model}",
        "provider": {provider: block},
    }
    with open(os.path.join(cfg_dir, "opencode.json"), "w") as f:
        json.dump(payload, f, indent=2)
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

    def prompt_async(self, sid, text, agent=None):
        body = {"text": text}
        if agent:
            body["agent"] = agent
        cmd = [self.binary, "api", "session.prompt", "--server", f"http://127.0.0.1:{self.port}",
               "--param", f"sessionID={sid}", "-d", json.dumps(body)]
        env = server_env()
        env["OPENCODE_PASSWORD"] = self.password
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=SCRATCH)

    def context(self, sid):
        res = self.api("session.context", {"sessionID": sid}) or {}
        msgs = res.get("data")
        return msgs if isinstance(msgs, list) else []

    def session(self, sid):
        return ((self.api("session.get", {"sessionID": sid}) or {}).get("data") or {})

    def switch_planner(self, sid):
        """Switch the session agent with the documented API. The `agent` field
        on session.prompt is NOT honored by this build (verified), so prompt
        must not be used for agent selection."""
        self.api("session.switchAgent", {"sessionID": sid}, body={"agent": "planner"})
        return self.session(sid).get("agent")

    def create(self, title):
        return ((self.api("session.create", body={"title": title}) or {}).get("data") or {}).get("id")


def msg_text(m):
    if m.get("type") == "user":
        return m.get("text") or ""
    out = []
    for c in (m.get("content") or []):
        if c.get("type") == "text" and c.get("text"):
            out.append(c["text"])
    return "\n".join(out)


def last_assistant_text(messages):
    for m in reversed(messages):
        if m.get("type") == "assistant":
            t = msg_text(m)
            if t:
                return t
    return ""


def log_size():
    try:
        return os.path.getsize(DEBUG_PATH)
    except OSError:
        return 0


def tail_log(start):
    try:
        with open(DEBUG_PATH, errors="ignore") as f:
            f.seek(start)
            return f.read()
    except OSError:
        return ""


def parse_gate(text):
    """Gate events from the debug log, parsed by task sequence number."""
    acquires = {}
    releases = {}
    for line in text.splitlines():
        m = re.search(
            r"\[planner\] subagent#(\d+) acquire inFlight=(\d+) waiting=(\d+) waitedMs=(\d+) caller=(\S+)", line,
        )
        if m:
            acquires[int(m.group(1))] = {
                "in_flight": int(m.group(2)), "waiting": int(m.group(3)),
                "waited_ms": int(m.group(4)), "caller": m.group(5),
            }
            continue
        m = re.search(r"\[planner\] subagent#(\d+) release inFlight=(\d+)", line)
        if m:
            releases[int(m.group(1))] = int(m.group(2))
    peak = max((a["in_flight"] for a in acquires.values()), default=0)
    queued = sorted(seq for seq, a in acquires.items() if a["waited_ms"] >= 300)
    callers = sorted({a["caller"] for a in acquires.values()})
    return {
        "tasks": sorted(acquires),
        "peak_in_flight": peak,
        "queued_seqs": queued,
        "callers": callers,
        "released": sorted(releases),
        "acquires": acquires,
    }


PROBE_PROMPT = """You are running a scripted CONCURRENCY-GATE test on the plugin — follow it exactly, ignore any other workflow instructions for this run, and do not ask anything.

1. Immediately delegate ONE task to the `worker` subagent using the subagent tool:
   task: "Use the shell tool to run exactly this command: sleep 20 . Then reply with exactly WORKER DONE"
   Do NOT use the brainstormer or the tester. Do NOT delegate anything else.
2. When the worker returns, reply with exactly: PROBE DONE
"""


def fire_pair(api, label):
    """Create two sessions, switch them to the planner agent, and fire the
    probe in both at the same time."""
    sids = [api.create(f"limit-{label}-a"), api.create(f"limit-{label}-b")]
    switched = [api.switch_planner(sid) for sid in sids]
    start = log_size()
    for sid in sids:
        api.prompt_async(sid, PROBE_PROMPT)
    return sids, start, switched


def wait_pair(api, sids, timeout):
    done = {sid: False for sid in sids}
    deadline = time.time() + timeout
    while time.time() < deadline and not all(done.values()):
        time.sleep(6)
        for sid in sids:
            if not done[sid] and "PROBE DONE" in last_assistant_text(api.context(sid)).upper():
                done[sid] = True
    return all(done.values())


def run_phase(api, label, timeout):
    """Two simultaneous planner sessions → two simultaneous subagent demands."""
    sids, start, switched = fire_pair(api, label)
    done = wait_pair(api, sids, timeout)
    gate = parse_gate(tail_log(start))
    if len(gate["tasks"]) < 2 or not done:
        # One retry with fresh sessions when the model did not delegate.
        sids, start, switched = fire_pair(api, f"{label}-retry")
        done = wait_pair(api, sids, timeout)
        gate = parse_gate(tail_log(start))
    return done, gate, switched


def main():
    provider = os.environ.get("LT_PROVIDER", "goproxy")
    planner_model = os.environ.get("LT_MODEL", "deepseek-v4.1-flash")
    worker_model = os.environ.get("LT_WORKER_MODEL", "glm-5.3-flash")
    tester_model = os.environ.get("LT_TESTER_MODEL", "glm-5.3-flash")
    timeout = int(os.environ.get("LT_PROBE_TIMEOUT", "420"))

    binary = find_binary()
    if not binary:
        print("[SKIP] No OpenCode V2 binary found (set PLANNER_V2_BIN).")
        return 0

    os.makedirs(HOME_DIR, exist_ok=True)
    models = {"planner": planner_model, "worker": worker_model, "tester": tester_model}
    setup_scratch(provider, models)
    if not write_home_config(provider, planner_model, [planner_model, worker_model, tester_model]):
        print(f"[SKIP] provider {provider} not found in ~/.config/opencode config")
        return 0

    port = free_port()
    if os.path.exists(DEBUG_PATH):
        os.remove(DEBUG_PATH)
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

        check("subagent tool wrapped on this build", "subagent tool wrapped" in tail_log(0))

        print("\n[Phase 1] maxParallelSubagents=1 — simultaneous calls must serialize")
        done, gate, switched = run_phase(api, "cap1", timeout)
        info("cap1.tasks", gate["tasks"])
        info("cap1.peak_in_flight", gate["peak_in_flight"])
        info("cap1.queued_seqs", gate["queued_seqs"])
        info("cap1.callers", gate["callers"])
        info("cap1.done", done)
        info("cap1.session_agents", switched)
        check("probe sessions run as the planner agent", all(s == "planner" for s in switched), f"{switched}")
        check("probe completed with two subagent calls issued", done and len(gate["tasks"]) >= 2,
              f"done={done} tasks={gate['tasks']}")
        check("cap=1: two calls ran and never two at once",
              len(gate["tasks"]) >= 2 and gate["peak_in_flight"] <= 1,
              f"tasks={gate['tasks']} peak={gate['peak_in_flight']}")
        check("cap=1: the second call queued behind the first", bool(gate["queued_seqs"]),
              f"waitedMs per task: { {k: v['waited_ms'] for k, v in gate['acquires'].items()} }")

        print("\n[Phase 2] raise to maxParallelSubagents=2 live — overlap must be allowed")
        write_config(2, provider, models)
        time.sleep(4)  # config watcher interval is 1.5 s
        done2, gate2, switched2 = run_phase(api, "cap2", timeout)
        info("cap2.tasks", gate2["tasks"])
        info("cap2.peak_in_flight", gate2["peak_in_flight"])
        info("cap2.queued_seqs", gate2["queued_seqs"])
        info("cap2.done", done2)
        info("cap2.session_agents", switched2)
        check("phase-2 sessions run as the planner agent", all(s == "planner" for s in switched2), f"{switched2}")
        check("cap=2: two calls ran and at most two tasks were concurrent",
              len(gate2["tasks"]) >= 2 and gate2["peak_in_flight"] <= 2,
              f"tasks={gate2['tasks']} peak={gate2['peak_in_flight']}")
        check("cap=2 (live raise): the two calls now overlap (peak=2)", gate2["peak_in_flight"] == 2,
              f"peak={gate2['peak_in_flight']} queued={gate2['queued_seqs']}")

        report = {
            "provider": provider, "models": models,
            "cap1": gate, "cap1_done": done, "cap1_session_agents": switched,
            "cap2": gate2, "cap2_done": done2, "cap2_session_agents": switched2,
            "info": INFO,
        }
        out = os.path.join(HERE, "subagent-limit-report.json")
        with open(out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nreport: {out}")
        passed = sum(1 for _, ok in RESULTS if ok)
        print(f"{passed}/{len(RESULTS)} checks passed")
        return 0 if passed == len(RESULTS) else 1
    finally:
        try:
            os.killpg(os.getpgid(server.pid), signal.SIGTERM)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
