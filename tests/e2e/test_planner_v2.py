#!/usr/bin/env python3
"""Planner V2 E2E on a real OpenCode 2.x server.

Boots `opencode serve` on a scratch project with the plugin as a discovered
plugin directory (server + TUI entrypoints together) and verifies the V2
contract:

  1. Plugin loads and is active; only the /plan server command is registered.
  2. All five agents exist (planner primary; brainstormer/worker/tester/vision
     subagents) and the definitions are installed into .opencode/agents/.
  3. The plugin is opt-in: a fresh session's agent is NOT planner, and no
     default agent is forced.
  4. MCP servers are left to OpenCode itself (native /mcps): the plugin never
     disables or enables them.
  5. `/plan` is the only server command; the config file drives the agent
     transforms and edits re-apply live through the watcher.
  6. The TUI entrypoint: typing /planner offers only /planner-config, which
     opens the configuration dialog; the vision/parallelism dialogs live in
     the ctrl+p palette.

No model provider is needed (no model turns are made).
Run: python3 tests/e2e/test_planner_v2.py   (set PLANNER_V2_BIN to override)
"""
import fcntl
import json
import os
import pty
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import termios
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

SCRATCH = "/tmp/planner-v2-test"
HOME_DIR = "/tmp/planner-v2-test-home"
PORT = int(os.environ.get("PLANNER_V2_PORT", "4742"))
MCP_SERVER = "testserver"
DEMO_PROVIDER = "demo"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    suffix = "" if ok else f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}: {name}{suffix}")


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
    return env


def wait_for_server(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def free_port():
    pattern = f"opencode serve --port {PORT}"
    subprocess.run(["pkill", "-9", "-f", pattern], capture_output=True)
    time.sleep(1)


def make_scratch():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    pkg = os.path.join(SCRATCH, ".opencode", "plugins", "opencode-planner")
    os.makedirs(pkg)
    shutil.copytree(os.path.join(ROOT, "src"), os.path.join(pkg, "src"))
    shutil.copytree(os.path.join(ROOT, "agents-v2"), os.path.join(pkg, "agents-v2"))
    for f in ("index.ts", "tui.ts", "package.json"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(pkg, f))
    # A fake MCP server: exits immediately, so enabling it fails fast while
    # disabled-by-default is observable.
    with open(os.path.join(SCRATCH, "opencode.json"), "w") as f:
        json.dump({
            "$schema": "https://opencode.ai/config.json",
            "mcp": {MCP_SERVER: {"type": "local", "command": ["true"]}},
        }, f, indent=2)
    # A demo provider in the private HOME so the /planner-config dialog has
    # deterministic models to pick from (nothing is ever called on it).
    cfg_dir = os.path.join(HOME_DIR, ".config", "opencode")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, "opencode.json"), "w") as f:
        json.dump({
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                DEMO_PROVIDER: {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Demo Provider",
                    "options": {"baseURL": "http://127.0.0.1:9/v1"},
                    "models": {
                        "worker-model": {"name": "Demo Worker Model"},
                    },
                }
            },
        }, f, indent=2)
    sh("git init -q -b main")
    sh("git config user.email t@t.com")
    sh("git config user.name t")
    with open(os.path.join(SCRATCH, "README.md"), "w") as f:
        f.write("# planner v2 e2e\n")
    sh("git add -A && git commit -qm init")


class V2:
    def __init__(self, binary, password):
        self.binary = binary
        self.password = password

    def api(self, op, params=None, body=None, timeout=90):
        cmd = [self.binary, "api", op, "--server", f"http://127.0.0.1:{PORT}"]
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

    def agents(self):
        res = self.api("agent.list") or {}
        return {a.get("id"): a for a in res.get("data", [])}

    def poll_agents(self, predicate, timeout=40):
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            last = self.agents()
            if predicate(last):
                return last
            time.sleep(1.5)
        return last

    def plugins(self):
        return {p.get("id"): p for p in (self.api("plugin.list") or {}).get("data", [])}

    def commands(self):
        return [c.get("name") for c in (self.api("command.list") or {}).get("data", [])]

    def mcp(self):
        return {m.get("name"): m.get("status", {}) for m in (self.api("mcp.list") or {}).get("data", [])}

    def create_session(self, title="s"):
        res = self.api("session.create", body={"title": title})
        return ((res or {}).get("data") or {}).get("id")

    def session(self, sid):
        return ((self.api("session.get", {"sessionID": sid}) or {}).get("data") or {})


def strip_ansi(text):
    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b[()][0-9AB]", "", text)
    text = re.sub(r"\x1b[>=]", "", text)
    return text


def tui_dialog_probe(binary):
    """Boot the TUI over a PTY and verify the planner surface:

    1. Typing `/planner` offers only /planner-config (no /planner-vision,
       /planner-status or /planner-help, each exactly once).
    2. `/planner-config` opens the configuration dialog.
    3. ctrl+p (command palette) still lists the vision/parallelism dialogs.
    """
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
    env = server_env()
    env.update({"TERM": "xterm-256color", "COLUMNS": "200", "LINES": "50"})
    proc = subprocess.Popen(
        [binary, "--standalone"], stdin=slave, stdout=slave, stderr=slave,
        cwd=SCRATCH, env=env, preexec_fn=os.setsid,
    )
    os.close(slave)

    def drain(seconds):
        out = b""
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                    if not chunk:
                        break
                    out += chunk
                except OSError:
                    break
        return out.decode("utf-8", errors="ignore")

    try:
        drain(20)
        os.write(master, b"/planner")
        time.sleep(1.5)
        completion = strip_ansi(drain(2)).lower()
        slash_ok = (
            completion.count("planner-config") == 1
            and "planner-vision" not in completion
            and "planner-status" not in completion
            and "planner-help" not in completion
        )

        dialog_ok = False
        for keys in (b"\r", b"\x1b[B\r"):
            os.write(master, keys)
            time.sleep(1.2)
            out = strip_ansi(drain(6)).lower()
            if "pick an agent" in out or ("brainstormer" in out and "max parallel subagents" in out):
                dialog_ok = True
                break
            os.write(master, b"\x1b")
            time.sleep(0.6)
            drain(1)
            os.write(master, b"\x15")
            time.sleep(0.3)
            os.write(master, b"/planner-config")
            time.sleep(1.2)
            drain(1)

        os.write(master, b"\x1b")
        time.sleep(0.6)
        drain(1)
        os.write(master, b"\x10")  # ctrl+p: command palette
        time.sleep(2)
        palette = strip_ansi(drain(3)).lower()
        palette_ok = "configure vision" in palette or "subagent parallelism" in palette
        os.write(master, b"\x1b")
        time.sleep(0.4)
        drain(1)
        return slash_ok, dialog_ok, palette_ok
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass
        try:
            os.close(master)
        except OSError:
            pass


def config_dialog_probe(binary):
    """Drive `/planner-config` in a real TUI: open the dialog, select Worker,
    pick the demo/worker-model; the write must land in the config file and be
    applied to the agent registry by the server watcher."""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
    env = server_env()
    env.update({"TERM": "xterm-256color", "COLUMNS": "200", "LINES": "50"})
    proc = subprocess.Popen(
        [binary, "--standalone"], stdin=slave, stdout=slave, stderr=slave,
        cwd=SCRATCH, env=env, preexec_fn=os.setsid,
    )
    os.close(slave)

    def drain(seconds):
        out = b""
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                    if not chunk:
                        break
                    out += chunk
                except OSError:
                    break
        return out.decode("utf-8", errors="ignore")

    try:
        drain(20)
        os.write(master, b"/planner-config")
        time.sleep(1.5)
        drain(1)
        os.write(master, b"\r")
        time.sleep(1.5)
        picker = strip_ansi(drain(4)).lower()
        picker_ok = "pick an agent" in picker

        os.write(master, b"\x1b[B")  # Brainstormer -> Worker
        time.sleep(0.4)
        os.write(master, b"\r")
        time.sleep(1.5)
        model_dialog = strip_ansi(drain(4)).lower()
        model_ok = "model for the worker agent" in model_dialog

        # Type-to-filter to the demo provider's single model, then submit.
        os.write(master, b"demo")
        time.sleep(0.8)
        drain(1)
        os.write(master, b"\x1b[B")
        time.sleep(0.3)
        os.write(master, b"\r")
        time.sleep(1.5)
        # Selecting the model opens the effort-variant dialog (the model
        # exposes variants); confirm it with the default/highlighted choice.
        drain(1)
        os.write(master, b"\r")
        time.sleep(4)  # toast + config write + watcher reload
        drain(2)
        return picker_ok, model_ok
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass
        try:
            os.close(master)
        except OSError:
            pass


def finish(server, logf):
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


def main():
    os.makedirs(HOME_DIR, exist_ok=True)
    binary = find_binary()
    if not binary:
        print("[SKIP] No OpenCode V2 binary found (set PLANNER_V2_BIN).")
        return 0

    print(f"\n[Phase 1] Scratch setup (binary: {binary})")
    make_scratch()
    check("plugin directory populated",
          os.path.isdir(os.path.join(SCRATCH, ".opencode", "plugins", "opencode-planner", "src")))

    print("\n[Phase 2] Boot V2 server")
    free_port()
    logf = open("/tmp/planner-v2-server.log", "w")
    server = subprocess.Popen(
        [binary, "serve", "--port", str(PORT), "--print-logs"],
        cwd=SCRATCH, stdout=logf, stderr=logf, env=server_env(), preexec_fn=os.setsid,
    )
    try:
        check("server started", wait_for_server())
        password = ""
        for _ in range(60):
            with open("/tmp/planner-v2-server.log") as f:
                for line in f:
                    if "server password" in line:
                        password = line.strip().split()[-1]
            if password:
                break
            time.sleep(0.5)
        check("server password captured", bool(password))
        api = V2(binary, password)
        api.plugins()  # boot the location
        time.sleep(2)

        plugins = api.plugins()
        check("planner plugin active", plugins.get("planner", {}).get("state", {}).get("status") == "active")

        print("\n[Phase 3] Agents + auto-installed definitions")
        agents = api.poll_agents(lambda a: all(x in a for x in ("planner", "worker", "tester")))
        check("all five agents registered",
              all(x in agents for x in ("planner", "brainstormer", "worker", "tester", "vision")))
        check("planner is primary", agents.get("planner", {}).get("mode") == "primary")
        check("subagents are subagent-mode",
              all(agents.get(x, {}).get("mode") == "subagent" for x in ("brainstormer", "worker", "tester", "vision")))
        check("planner model never pinned", not agents.get("planner", {}).get("model"))
        check("no models pinned without a config file",
              not agents.get("worker", {}).get("model") and not agents.get("tester", {}).get("model"))
        check("agent definitions installed to .opencode/agents",
              all(os.path.exists(os.path.join(SCRATCH, ".opencode", "agents", f"{a}.md"))
                  for a in ("planner", "brainstormer", "worker", "tester", "vision")))
        check("specs directory created for the brainstormer",
              os.path.isdir(os.path.join(SCRATCH, "docs", "superpowers", "specs")))
        with open(os.path.join(SCRATCH, ".opencode", "agents", "planner.md")) as f:
            planner_md = f.read()
        check("planner.md is the V2 format", "permissions:" in planner_md and "mode: primary" in planner_md)

        print("\n[Phase 4] Opt-in: sessions are normal")
        sid = api.create_session("normal")
        check("session created", bool(sid))
        info = api.session(sid)
        check("new session is not on planner", info.get("agent") != "planner",
              f"agent={info.get('agent')!r}")

        print("\n[Phase 5] Server commands + config file drives the transforms")
        cmds = api.commands()
        check("plan is a server command", "plan" in cmds)
        for gone in ["planner-config", "planner-vision", "planner-status", "planner-help", "planner-mcp"]:
            check(f"command /{gone} removed from the server", gone not in cmds)
        check("no models pinned before a config file exists",
              not (api.agents().get("worker", {}).get("model")))

        # The TUI dialog writes the config file; simulate that and verify the
        # server watcher re-reads it and re-applies the agent transforms.
        cfg = {
            "models": {
                "planner": "",
                "brainstormer": "smoke/brainstormer-model",
                "worker": "smoke/worker-model",
                "tester": "smoke/tester-model",
            },
            "variants": {"worker": "high"},
            "maxCycles": 3,
            "maxParallelSubagents": 2,
            "screenshotNotes": False,
            "vision": {"enabled": True, "model": "smoke/vision-model", "variant": "max", "mode": "agent"},
        }
        with open(os.path.join(SCRATCH, ".opencode", "planner-plugin.json"), "w") as f:
            json.dump(cfg, f, indent=2)

        agents = api.poll_agents(
            lambda a: (a.get("worker", {}).get("model") or {}).get("variant") == "high"
            and (a.get("tester", {}).get("model") or {}).get("id") == "tester-model"
            and (a.get("vision", {}).get("model") or {}).get("id") == "vision-model"
            and (a.get("brainstormer", {}).get("model") or {}).get("id") == "brainstormer-model",
        )
        w = agents.get("worker", {}).get("model") or {}
        t = agents.get("tester", {}).get("model") or {}
        v = agents.get("vision", {}).get("model") or {}
        b = agents.get("brainstormer", {}).get("model") or {}
        check("worker model + effort variant applied",
              f"{w.get('providerID')}/{w.get('id')}#{w.get('variant')}" == "smoke/worker-model#high")
        check("tester model applied", f"{t.get('providerID')}/{t.get('id')}" == "smoke/tester-model")
        check("brainstormer model applied independently (not inherited)",
              f"{b.get('providerID')}/{b.get('id')}" == "smoke/brainstormer-model")
        check("vision model + variant applied (agent mode)",
              f"{v.get('providerID')}/{v.get('id')}#{v.get('variant')}" == "smoke/vision-model#max")

        # A second edit must apply live too (the watcher, not just boot).
        cfg["models"]["worker"] = "smoke/worker-model-2"
        with open(os.path.join(SCRATCH, ".opencode", "planner-plugin.json"), "w") as f:
            json.dump(cfg, f, indent=2)
        agents = api.poll_agents(lambda a: (a.get("worker", {}).get("model") or {}).get("id") == "worker-model-2")
        check("config edits re-apply live via the watcher",
              (agents.get("worker", {}).get("model") or {}).get("id") == "worker-model-2")

        print("\n[Phase 5b] /planner-config dialog applies immediately")
        try:
            picker_ok, model_ok = config_dialog_probe(binary)
        except Exception as e:
            print(f"    (dialog probe error: {e})")
            picker_ok = model_ok = False
        check("dialog opens the agent picker", picker_ok)
        check("dialog opens the model picker", model_ok)
        with open(os.path.join(SCRATCH, ".opencode", "planner-plugin.json")) as f:
            cfg_now = json.load(f)
        check("dialog wrote the worker model to the config file",
              cfg_now["models"]["worker"] == f"{DEMO_PROVIDER}/worker-model",
              f"got {cfg_now['models']['worker']!r}")
        agents = api.poll_agents(lambda a: (a.get("worker", {}).get("model") or {}).get("id") == "worker-model")
        check("dialog change applied to the agent immediately (watcher)",
              (agents.get("worker", {}).get("model") or {}).get("id") == "worker-model")

        print("\n[Phase 6] MCP servers are left to OpenCode (no plugin override)")
        time.sleep(2)
        mcp = api.mcp()
        check("test MCP server exists in the config", MCP_SERVER in mcp)
        check("plugin does not force-disable MCP servers", (mcp.get(MCP_SERVER) or {}).get("status") != "disabled",
              f"status={mcp.get(MCP_SERVER)!r}")

        print("\n[Phase 7] TUI: /planner-config opens the dialog, palette intact")
        try:
            slash_ok, dialog_ok, palette_ok = tui_dialog_probe(binary)
        except Exception as e:
            print(f"    (TUI probe error: {e})")
            slash_ok = dialog_ok = palette_ok = False
        check("slash offers only planner-config (no vision/status/help)", slash_ok)
        check("/planner-config opens the configuration dialog", dialog_ok)
        check("command palette still lists the vision/parallelism dialogs", palette_ok)

        return finish(server, logf)
    finally:
        pass


if __name__ == "__main__":
    sys.exit(main())
