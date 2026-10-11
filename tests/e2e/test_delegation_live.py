#!/usr/bin/env python3
"""Live delegation-watchdog E2E (R2) — real model, real OpenCode 2 server.

Boots `opencode serve` on a scratch project with the plugin (PLANNER_DEBUG=1)
and verifies the mandatory-delegation contract on real planner sessions:

  Scenario A — a trivial question ("Answer with exactly: PONG") must still go
  through the brainstormer subagent FIRST; a TRIVIAL verdict may end the turn
  with no nudge (a READY verdict must trigger the worker/tester nudge).
  Scenario B — a deterministic zero-delegation turn (a test-only helper plugin
  makes the first 2 subagent calls of the session fail, and only COMPLETED
  calls count as delegation): the watchdog must nudge, the nudge must be
  visible, and a later turn must delegate for real. Nudges are bounded to 2.
  Scenario C — with enforceDelegation=false the same zero-delegation turn must
  NOT be nudged (checked only after the turn ended + the settle window).
  Scenario D — a session that never recovers (every subagent call fails) is
  nudged exactly twice; the cap holds and no third nudge follows.

This suite makes real model calls and takes minutes — it is NOT part of
run_all.sh. The provider block is read from the user's ~/.config/opencode.

Run: python3 -u tests/e2e/test_delegation_live.py
Env: LT_PROVIDER (goproxy), LT_MODEL, LT_BRAINSTORMER_MODEL, LT_WORKER_MODEL,
     LT_TESTER_MODEL, PLANNER_V2_BIN, LT_SCENARIO_TIMEOUT (default 420).
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

SCRATCH = "/tmp/planner-delegation-test"
HOME_DIR = "/tmp/planner-delegation-test-home"
LOG_PATH = "/tmp/planner-delegation-test-server.log"
DEBUG_LOG = "/tmp/planner-delegation-test-debug.log"
NUDGE_MARKER = "[planner-delegation-check]"
ARM_FILE = "/tmp/planner-delegation-test.arm"
NEVER_ARM_FILE = "/tmp/planner-delegation-test-never.arm"

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
    env["PLANNER_DEBUG_FILE"] = DEBUG_LOG
    env["WATCHDOG_ARM_FILE"] = ARM_FILE
    env["WATCHDOG_NEVER_ARM_FILE"] = NEVER_ARM_FILE
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


def write_config(provider, models, enforce=True):
    cfg = {
        "models": {
            "planner": "",
            "brainstormer": f"{provider}/{models['brainstormer']}",
            "worker": f"{provider}/{models['worker']}",
            "tester": f"{provider}/{models['tester']}",
        },
        "variants": {},
        "maxCycles": 1,
        "maxParallelSubagents": 1,
        "enforceDelegation": enforce,
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
    # Test-only helper: hides the subagent tool for the first request of a
    # planner session while the arm file exists (deterministic zero-delegation).
    shutil.copy(os.path.join(HERE, "watchdog_probe_plugin.ts"),
                os.path.join(SCRATCH, ".opencode", "plugins", "watchdog-probe.ts"))
    write_config(provider, models)
    sh("git init -q -b main")
    sh("git config user.email t@t.com")
    sh("git config user.name t")
    with open(os.path.join(SCRATCH, "README.md"), "w") as f:
        f.write("# planner delegation watchdog live test\n")
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

    def context(self, sid):
        res = self.api("session.context", {"sessionID": sid}) or {}
        msgs = res.get("data")
        return msgs if isinstance(msgs, list) else []

    def children(self, sid):
        res = self.api("session.children", {"sessionID": sid})
        if res and isinstance(res.get("data"), list):
            return res["data"]
        res = self.api("session.list")
        if res and isinstance(res.get("data"), list):
            return [s for s in res["data"] if s.get("parentID") == sid]
        return []


def log_text(start=0):
    try:
        with open(DEBUG_LOG, errors="ignore") as f:
            f.seek(start)
            return f.read()
    except OSError:
        return ""


def log_size():
    try:
        return os.path.getsize(DEBUG_LOG)
    except OSError:
        return 0


def session_log_lines(text, sid):
    return [ln for ln in text.splitlines() if sid and sid in ln]


def parse_watchdog(lines):
    calls = []
    nudges = []
    verdict = None
    for ln in lines:
        m = re.search(r"watchdog: subagent call #(\d+) target=(\S+) session=", ln)
        if m:
            calls.append({"n": int(m.group(1)), "target": m.group(2)})
            continue
        m = re.search(r"watchdog: nudge (\d+)/(\d+) \(([^)]*)\) session=", ln)
        if m:
            nudges.append({"n": int(m.group(1)), "max": int(m.group(2)), "reason": m.group(3)})
            continue
        m = re.search(r"watchdog: brainstormer result -> (\w+)", ln)
        if m:
            verdict = m.group(1)
    return {"calls": calls, "nudges": nudges, "verdict": verdict}


def last_assistant_text(messages):
    for m in reversed(messages):
        if m.get("type") != "assistant":
            continue
        out = []
        for c in (m.get("content") or []):
            if c.get("type") == "text" and c.get("text"):
                out.append(c["text"])
        if out:
            return "\n".join(out)
    return ""


def wait_until(predicate, timeout, interval=6):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


SCEN_A_PROMPT = "Answer with exactly: PONG"
SCEN_B_PROMPT = "Give me a one-line friendly greeting."
PROBE_FAIL_MSG = "[watchdog-probe] failing subagent call"


def run_scenario_a(api, timeout):
    start = log_size()
    sid = api.create("delegation-a")
    agent = api.switch_planner(sid)
    api.prompt_async(sid, SCEN_A_PROMPT)
    answered = wait_until(lambda: "PONG" in last_assistant_text(api.context(sid)).upper(), timeout)
    # Wait for the brainstormer call to COMPLETE (the model may answer directly
    # first and be nudged into delegating, so the call can start late).
    wait_until(
        lambda: any("brainstormer result ->" in ln and sid in ln for ln in log_text(start).splitlines()),
        min(timeout, 240),
        interval=4,
    )
    time.sleep(3)
    lines = session_log_lines(log_text(start), sid)
    wd = parse_watchdog(lines)
    verdict_idx = next((i for i, ln in enumerate(lines) if "brainstormer result ->" in ln), None)
    nudge_after_verdict = (
        any("watchdog: nudge" in ln for ln in lines[verdict_idx + 1:]) if verdict_idx is not None else False
    )
    kids = api.children(sid)
    return {"sid": sid, "agent": agent, "answered": answered, "watchdog": wd, "children": kids,
            "nudge_after_brainstormer": nudge_after_verdict}


def run_scenario_b(api, timeout):
    """Deterministic zero-delegation turn: the test-only probe plugin fails the
    first 2 subagent calls of the session while armed, so no COMPLETED
    delegation exists in the first turn(s). The watchdog must nudge, the nudge
    must be visible, and a later turn must delegate for real (the failure
    budget is exhausted)."""
    with open(ARM_FILE, "w") as f:
        f.write("armed\n")
    start = log_size()
    sid = api.create("delegation-b")
    agent = api.switch_planner(sid)
    api.prompt_async(sid, SCEN_B_PROMPT)
    nudged = wait_until(
        lambda: any("watchdog: nudge" in ln and sid in ln for ln in log_text(start).splitlines()),
        timeout,
        interval=3,
    )
    delegated_after = False
    if nudged:
        delegated_after = wait_until(
            lambda: any("watchdog: subagent call" in ln and sid in ln for ln in log_text(start).splitlines()),
            min(timeout, 300),
            interval=4,
        )
    time.sleep(3)
    lines = session_log_lines(log_text(start), sid)
    wd = parse_watchdog(lines)
    raw = json.dumps(api.context(sid))
    probe_failed = any(PROBE_FAIL_MSG in ln for ln in lines)
    if os.path.exists(ARM_FILE):
        os.remove(ARM_FILE)
    return {"sid": sid, "agent": agent, "probe_failed": probe_failed, "nudged": nudged,
            "delegated_after_nudge": delegated_after, "nudge_visible": NUDGE_MARKER in raw,
            "watchdog": wd}


def run_scenario_c(api, provider, models, timeout):
    """With enforceDelegation=false the same deterministic zero-delegation turn
    must NOT be nudged."""
    with open(ARM_FILE, "w") as f:
        f.write("armed\n")
    write_config(provider, models, enforce=False)
    time.sleep(4)  # config watcher applies the toggle
    start = log_size()
    sid = api.create("delegation-c")
    agent = api.switch_planner(sid)
    api.prompt_async(sid, SCEN_B_PROMPT)
    answered = wait_until(lambda: bool(last_assistant_text(api.context(sid))), min(timeout, 300), interval=5)
    # Wait for the turn to actually END (execution.succeeded) plus the whole
    # settle window + margin, so a wrongly-issued nudge would be visible here.
    wait_until(
        lambda: any("session.execution.succeeded" in ln and sid in ln for ln in log_text(start).splitlines()),
        min(timeout, 240),
        interval=3,
    )
    time.sleep(9)
    lines = session_log_lines(log_text(start), sid)
    wd = parse_watchdog(lines)
    probe_failed = any(PROBE_FAIL_MSG in ln for ln in lines)
    if os.path.exists(ARM_FILE):
        os.remove(ARM_FILE)
    write_config(provider, models, enforce=True)
    time.sleep(2)
    return {"sid": sid, "agent": agent, "answered": answered, "probe_failed": probe_failed, "watchdog": wd}


def run_scenario_d(api, timeout):
    """A session that never recovers (every subagent call fails): the watchdog
    must nudge exactly twice and then stop."""
    with open(NEVER_ARM_FILE, "w") as f:
        f.write("never\n")
    start = log_size()
    sid = api.create("delegation-d")
    agent = api.switch_planner(sid)
    api.prompt_async(sid, SCEN_B_PROMPT)
    got_two = wait_until(
        lambda: len([ln for ln in log_text(start).splitlines()
                     if "watchdog: nudge" in ln and sid in ln]) >= 2,
        timeout,
        interval=4,
    )
    time.sleep(12)  # a third nudge would land within one settle window + margin
    lines = session_log_lines(log_text(start), sid)
    wd = parse_watchdog(lines)
    if os.path.exists(NEVER_ARM_FILE):
        os.remove(NEVER_ARM_FILE)
    return {"sid": sid, "agent": agent, "got_two": got_two, "watchdog": wd}


def main():
    provider = os.environ.get("LT_PROVIDER", "goproxy")
    planner_model = os.environ.get("LT_MODEL", "deepseek-v4.1-flash")
    brainstormer_model = os.environ.get("LT_BRAINSTORMER_MODEL", "glm-5.3-flash")
    worker_model = os.environ.get("LT_WORKER_MODEL", "glm-5.3-flash")
    tester_model = os.environ.get("LT_TESTER_MODEL", "glm-5.3-flash")
    timeout = int(os.environ.get("LT_SCENARIO_TIMEOUT", "420"))

    binary = find_binary()
    if not binary:
        print("[SKIP] No OpenCode V2 binary found (set PLANNER_V2_BIN).")
        return 0

    os.makedirs(HOME_DIR, exist_ok=True)
    models = {"planner": planner_model, "brainstormer": brainstormer_model,
              "worker": worker_model, "tester": tester_model}
    setup_scratch(provider, models)
    if not write_home_config(provider, planner_model,
                             [planner_model, brainstormer_model, worker_model, tester_model]):
        print(f"[SKIP] provider {provider} not found in ~/.config/opencode config")
        return 0

    port = free_port()
    if os.path.exists(DEBUG_LOG):
        os.remove(DEBUG_LOG)
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

        print("\n[Scenario A] a trivial question still goes through the brainstormer")
        a = run_scenario_a(api, timeout)
        info("a.agent", a["agent"])
        info("a.watchdog", a["watchdog"])
        info("a.children", [(c.get("agent"), c.get("title")) for c in a["children"]])
        check("scenario A ran on the planner agent", a["agent"] == "planner", f"{a['agent']}")
        check("scenario A answered", a["answered"])
        targets = [c["target"] for c in a["watchdog"]["calls"]]
        check("trivial request was delegated to the brainstormer first", "brainstormer" in targets, f"targets={targets}")
        check("only the brainstormer ran for the trivial request (no worker/tester)",
              all(t not in ("worker", "tester") for t in targets), f"targets={targets}")
        check("scenario A nudges are bounded to 2", len(a["watchdog"]["nudges"]) <= 2,
              f"nudges={a['watchdog']['nudges']}")
        # The model may answer directly first (corrected by one nudge) or
        # delegate straight away; after a COMPLETED brainstormer call the
        # verdict decides: TRIVIAL must end the turn, READY must trigger the
        # worker/tester nudge (rule 2).
        if a["watchdog"]["verdict"] == "ready":
            check("READY verdict nudged the missing worker/tester (rule 2)",
                  any(n["reason"] == "spec READY but no worker/tester" for n in a["watchdog"]["nudges"]),
                  f"nudges={a['watchdog']['nudges']}")
        else:
            check("no nudge after the brainstormer completed (TRIVIAL ends the turn)",
                  a["nudge_after_brainstormer"] is False, f"nudges={a['watchdog']['nudges']}")

        print("\n[Scenario B] a zero-delegation turn is nudged and resumed (deterministic)")
        b = run_scenario_b(api, timeout)
        info("b.agent", b["agent"])
        info("b.probe_failed", b["probe_failed"])
        info("b.watchdog", b["watchdog"])
        info("b.nudge_visible", b["nudge_visible"])
        check("scenario B ran on the planner agent", b["agent"] == "planner", f"{b['agent']}")
        check("the probe made the first subagent calls fail", b["probe_failed"])
        check("the no-delegation turn was nudged", b["nudged"], f"nudges={b['watchdog']['nudges']}")
        check("the nudge is visible in the session", b["nudge_visible"])
        check("nudges are bounded to 2 per turn", len(b["watchdog"]["nudges"]) <= 2,
              f"nudges={b['watchdog']['nudges']}")
        targets_b = [c["target"] for c in b["watchdog"]["calls"]]
        check("a later turn delegated for real after the nudge", b["delegated_after_nudge"],
              f"targets={targets_b}")

        print("\n[Scenario C] enforceDelegation=false suppresses the nudge")
        c = run_scenario_c(api, provider, models, timeout)
        info("c.agent", c["agent"])
        info("c.probe_failed", c["probe_failed"])
        info("c.watchdog", c["watchdog"])
        check("scenario C ran on the planner agent", c["agent"] == "planner", f"{c['agent']}")
        check("scenario C probe failed a subagent call", c["probe_failed"])
        check("scenario C was answered without a completed delegation", c["answered"])
        check("enforceDelegation=false suppresses the nudge", len(c["watchdog"]["nudges"]) == 0,
              f"nudges={c['watchdog']['nudges']}")

        print("\n[Scenario D] a session that never recovers is nudged exactly twice")
        d = run_scenario_d(api, timeout)
        info("d.agent", d["agent"])
        info("d.watchdog", d["watchdog"])
        check("scenario D ran on the planner agent", d["agent"] == "planner", f"{d['agent']}")
        check("the never-recovering turn was nudged twice", d["got_two"], f"nudges={d['watchdog']['nudges']}")
        check("no third nudge — the cap holds", len(d["watchdog"]["nudges"]) == 2, f"nudges={d['watchdog']['nudges']}")

        report = {"provider": provider, "models": models, "scenario_a": a, "scenario_b": b,
                  "scenario_c": c, "scenario_d": d, "info": INFO}
        out = os.path.join(HERE, "delegation-report.json")
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
