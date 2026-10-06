#!/usr/bin/env python3
"""Long-session E2E for the planner plugin on OpenCode 2 — real model.

Boots `opencode serve` on a scratch project with the plugin as a discovered
plugin directory, then:

  1. Runs a NORMAL session (default agent) with a trivial prompt and verifies
     it stays normal: not on the planner, no child sessions, small payload —
     the "no planner/MCP tokens at session start" evidence.
  2. Runs a PLANNER session: the planner delegates to the worker subagent
     (writes notes-<label>.txt) and the tester subagent (verifies) for N
     cycles, ending with ALL CYCLES DONE; children, STATUS lines, files and
     errors are reported.

Usage:
  python3 tests/e2e/long_session_test.py --label A --cycles 2
Options: --provider, --model (planner/session), --brainstormer-model, --worker-model,
         --tester-model, --timeout.
Note: goproxy/muse-spark-1.3-contributor is blocked until the GoProxy workspace
enables "paid endpoints that train on request data" in its Privacy settings; the
defaults use models that work everywhere (deepseek-v4.1-flash, glm-5.3-flash).
"""
import argparse
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

SCRATCH = "/tmp/planner-longtest"
HOME_DIR = "/tmp/planner-longtest-home"


def sh(cmd, cwd=SCRATCH, env=None):
    return subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, env=env)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def find_binary():
    explicit = os.environ.get("PLANNER_V2_BIN") or os.environ.get("LT_BIN")
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


def setup_config(planner_models):
    shutil.rmtree(SCRATCH, ignore_errors=True)
    pkg = os.path.join(SCRATCH, ".opencode", "plugins", "opencode-planner")
    os.makedirs(pkg)
    shutil.copytree(os.path.join(ROOT, "src"), os.path.join(pkg, "src"))
    shutil.copytree(os.path.join(ROOT, "agents-v2"), os.path.join(pkg, "agents-v2"))
    for f in ("index.ts", "tui.ts", "package.json"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(pkg, f))
    # Per-agent models (the planner itself follows the session model).
    with open(os.path.join(SCRATCH, ".opencode", "planner-plugin.json"), "w") as f:
        json.dump({
            "models": {
                "planner": "",
                "brainstormer": planner_models["brainstormer"],
                "worker": planner_models["worker"],
                "tester": planner_models["tester"],
            },
            "variants": {},
            "maxCycles": 2,
            "maxParallelSubagents": 1,
            "vision": {"enabled": False, "model": "", "mode": "agent"},
        }, f, indent=2)
    sh("git init -q -b main")
    sh("git config user.email t@t.com")
    sh("git config user.name t")
    with open(os.path.join(SCRATCH, "README.md"), "w") as f:
        f.write("# planner long session test\n")
    sh("git add -A && git commit -qm init")


def write_home_config(provider, model, model_ids):
    cfg_dir = os.path.join(HOME_DIR, ".config", "opencode")
    os.makedirs(cfg_dir, exist_ok=True)
    block = real_provider_block(provider)
    if not block:
        return False
    block = json.loads(json.dumps(block))
    models = block.setdefault("models", {})
    # The proxy may serve ids the user's config does not list by name; declare
    # them so opencode accepts the model refs used by this test.
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

    def children(self, sid):
        res = self.api("session.children", {"sessionID": sid})
        if res and isinstance(res.get("data"), list):
            return res["data"]
        res = self.api("session.list")
        if res and isinstance(res.get("data"), list):
            return [s for s in res["data"] if s.get("parentID") == sid]
        return []


def msg_text(m):
    """Text of one V2 session message (flat type/content shape)."""
    if m.get("type") == "user":
        return m.get("text") or ""
    out = []
    for c in (m.get("content") or []):
        if c.get("type") == "text" and c.get("text"):
            out.append(c["text"])
    return "\n".join(out)


def stats(messages):
    payload = sum(len(json.dumps(m)) for m in messages)
    errors = []
    text = ""
    tokens = {"input": 0, "output": 0, "reasoning": 0}
    for m in messages:
        if m.get("type") != "assistant":
            continue
        if m.get("error"):
            e = m["error"]
            errors.append(str((e.get("data") or {}).get("message") or e.get("name") or e)[:120])
        t = msg_text(m)
        if t:
            text = t
        tk = m.get("tokens") or {}
        for k in tokens:
            tokens[k] += tk.get(k) or 0
    return {"messages": len(messages), "payload_kb": round(payload / 1000, 1),
            "errors": errors, "last": text, "tokens": tokens}


def assistant_agent(messages):
    for m in reversed(messages):
        if m.get("type") == "assistant" and m.get("agent"):
            return m["agent"]
    return None


def all_text(messages):
    out = []
    for m in messages:
        t = msg_text(m)
        if t:
            out.append(t)
    return "\n".join(out)


def verify_greet(label):
    notes = os.path.join(SCRATCH, f"notes-{label}.txt")
    greet = os.path.join(SCRATCH, "tools", "greet.py")
    return {
        "notes_exists": os.path.exists(notes),
        "notes_text": open(notes).read() if os.path.exists(notes) else "",
        "greet_exists": os.path.exists(greet),
        "greet_text": open(greet).read()[:1000] if os.path.exists(greet) else "",
    }


def verify_mario(label):
    """Static verification of the Mario card game (no browser needed)."""
    files = {f: os.path.exists(os.path.join(SCRATCH, f)) for f in ("index.html", "styles.css", "game.js")}
    res = {"files": files, "js_syntax_ok": False, "html_refs": False, "has_mario": False,
           "has_match": False, "has_score": False, "has_moves": False, "has_restart": False,
           "readme_ok": False}
    js = ""
    if files["game.js"]:
        proc = subprocess.run(["node", "--check", os.path.join(SCRATCH, "game.js")],
                              capture_output=True, text=True)
        res["js_syntax_ok"] = proc.returncode == 0
        res["js_syntax_err"] = proc.stderr[-300:]
        js = open(os.path.join(SCRATCH, "game.js")).read()
    html = open(os.path.join(SCRATCH, "index.html")).read() if files["index.html"] else ""
    readme_path = os.path.join(SCRATCH, "README.md")
    readme = open(readme_path).read() if os.path.exists(readme_path) else ""
    blob = (html + js).lower()
    res.update({
        "html_refs": ("styles.css" in html and "game.js" in html),
        "has_mario": "mario" in blob,
        "has_match": "match" in js.lower(),
        "has_score": "score" in js.lower(),
        "has_moves": "move" in js.lower(),
        "has_restart": "restart" in blob,
        "readme_ok": len(readme.strip()) > 50,
    })
    return res


TASKS = {
    "greet": {
        "task_text": (
            "create `tools/greet.py` (a small CLI that prints a greeting; support an optional "
            "`--name NAME` argument) and document it in README.md. Also maintain the run log "
            "`notes-{label}.txt`: it starts empty and each cycle appends exactly one new line "
            "`cycle i`."
        ),
        "cycle": lambda i: [
            "Delegate to the worker subagent: read the spec first; make sure `tools/greet.py` matches "
            f"the spec/requirements, and append one new line `cycle {i}` to `notes-{{label}}.txt`.",
            "When the worker returns, delegate to the tester subagent: verify `notes-{label}.txt` "
            f"contains the line `cycle {i}` and that `tools/greet.py` exists (and matches the "
            "requirements); the tester replies with a STATUS line.",
        ],
        "verify": verify_greet,
        "success": lambda v: v["notes_exists"] and "cycle 1" in v["notes_text"] and v["greet_exists"],
    },
    "mario": {
        "task_text": (
            "Build a **Mario-themed card game** as a static web app (no build step, no third-party "
            "dependencies): `index.html`, `styles.css`, `game.js`. A deck of Mario characters (Mario, "
            "Luigi, Peach, Toad, Bowser, Yoshi, …), a shuffled board of face-down cards, click to flip, "
            "two matching cards stay revealed, non-matching cards flip back, and the game tracks moves "
            "and wins. Verified with `node --check game.js` and file inspection (no browser)."
        ),
        "cycle": lambda i: {
            1: [
                "Delegate to the worker subagent: read the spec first; implement the CORE game in "
                "`game.js` (deck of Mario characters, shuffle, card flip, pair matching), the board "
                "container in `index.html`, and the card styles in `styles.css`.",
                "When the worker returns, delegate to the tester subagent: verify the three files exist, "
                "`node --check game.js` passes, and `index.html` links `styles.css` and `game.js`; the "
                "tester replies with a STATUS line.",
            ],
            2: [
                "Delegate to the worker subagent: add the moves counter, the score, the win state when "
                "all pairs are matched, and a restart button; keep `node --check game.js` passing.",
                "When the worker returns, delegate to the tester subagent: verify the moves counter, "
                "score, win state and restart exist and the JS is still syntactically valid; the tester "
                "replies with a STATUS line.",
            ],
            3: [
                "Delegate to the worker subagent: polish the Mario theming (character emoji/labels, "
                "colors) and write `README.md` with the game rules and how to run it.",
                "When the worker returns, delegate to the tester subagent: verify README.md documents "
                "how to play, the game files still pass `node --check game.js`, and Mario branding is "
                "present; the tester replies with a STATUS line.",
            ],
        }[i],
        "verify": verify_mario,
        "success": lambda v: (
            all(v["files"].values())
            and v["js_syntax_ok"]
            and v["html_refs"]
            and v["has_mario"]
            and v["has_match"]
            and v["has_score"]
            and v["has_moves"]
            and v["has_restart"]
            and v["readme_ok"]
        ),
    },
}


def build_task(task_key, label, cycles):
    spec = TASKS[task_key]
    lines = [
        f"You are running a scripted end-to-end test (label {label}). Follow this workflow exactly and do not ask the user anything.",
        "",
        "Step 0 — Brainstormer: delegate to the brainstormer subagent with the task below.",
        "  Tell it explicitly: do NOT request input (never reply STATUS: NEEDS_INPUT); write the spec with documented assumptions and reply STATUS: READY with the SPEC path.",
        "  If it replies STATUS: NEEDS_INPUT anyway, answer its questions yourself with documented assumptions and re-delegate until it returns STATUS: READY — never wait for the user.",
        "  When it returns, use its SPEC path + requirements as the source of truth for the worker. READ the spec file yourself before delegating.",
        "",
        f"Task to spec and implement: {spec['task_text']}",
        "",
    ]
    for i in range(1, cycles + 1):
        lines += [f"Cycle {i}:"] + [f"  {j + 1}. {step.format(label=label)}" for j, step in enumerate(spec["cycle"](i))]
    lines += [
        "After the last cycle, summarize what each subagent reported and end your message with the exact text: ALL CYCLES DONE",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="A")
    ap.add_argument("--task", default="greet", choices=sorted(TASKS.keys()))
    ap.add_argument("--cycles", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--provider", default=os.environ.get("LT_PROVIDER", "goproxy"))
    ap.add_argument("--model", default=os.environ.get("LT_MODEL", "deepseek-v4.1-flash"))
    ap.add_argument("--brainstormer-model", default=os.environ.get("LT_BRAINSTORMER_MODEL", "deepseek-v4.1-flash"))
    ap.add_argument("--worker-model", default=os.environ.get("LT_WORKER_MODEL", "deepseek-v4.1-flash"))
    ap.add_argument("--tester-model", default=os.environ.get("LT_TESTER_MODEL", "glm-5.3-flash"))
    args = ap.parse_args()

    binary = find_binary()
    if not binary:
        print("[SKIP] No OpenCode V2 binary found (set PLANNER_V2_BIN).")
        return 0
    os.makedirs(HOME_DIR, exist_ok=True)
    planner_models = {
        "brainstormer": f"{args.provider}/{args.brainstormer_model}",
        "worker": f"{args.provider}/{args.worker_model}",
        "tester": f"{args.provider}/{args.tester_model}",
    }
    setup_config(planner_models)
    if not write_home_config(args.provider, args.model,
                             [args.model, args.brainstormer_model, args.worker_model, args.tester_model]):
        print(f"[SKIP] provider {args.provider} not found in ~/.config/opencode config")
        return 0

    port = free_port()
    logf = open(f"/tmp/planner-longtest-{args.label}-server.log", "w")
    server = subprocess.Popen(
        [binary, "serve", "--port", str(port), "--print-logs"],
        cwd=SCRATCH, stdout=logf, stderr=logf, env=server_env(), preexec_fn=os.setsid,
    )
    report = {
        "label": args.label,
        "model": f"{args.provider}/{args.model}",
        "agent_models": planner_models,
        "cycles": args.cycles,
    }
    try:
        password = ""
        deadline = time.time() + 60
        while time.time() < deadline and not password:
            for line in open(f"/tmp/planner-longtest-{args.label}-server.log"):
                if "server password" in line:
                    password = line.strip().split()[-1]
            if not password:
                time.sleep(0.5)
        if not password:
            print("server failed to boot")
            return 1
        api = V2(binary, password, port)
        t0 = time.time()
        print(f"[{args.label}] server up (port {port})")

        # ------------------------------------------------------------- normal
        print(f"[{args.label}] normal session (default agent)…")
        sid_normal = ((api.api("session.create", body={"title": f"normal-{args.label}"}) or {}).get("data") or {}).get("id")
        api.prompt_async(sid_normal, "Reply with exactly: PONG")
        deadline = time.time() + 300
        normal = {}
        while time.time() < deadline:
            messages = api.context(sid_normal)
            normal = stats(messages)
            if "PONG" in normal["last"].upper():
                break
            time.sleep(5)
        info = api.session(sid_normal)
        report["normal"] = {
            **{k: v for k, v in normal.items() if k != "errors"},
            "errors": len(normal["errors"]),
            "agent": info.get("agent") or assistant_agent(api.context(sid_normal)),
            "children": len(api.children(sid_normal)),
            "duration_s": int(time.time() - t0),
        }
        print(f"[{args.label}] normal: agent={report['normal']['agent']} children={report['normal']['children']} "
              f"payload={normal['payload_kb']}KB tokens={normal['tokens']} last={normal['last'][:60]!r}")

        # ------------------------------------------------------------ planner
        print(f"[{args.label}] planner session ({args.task}, {args.cycles} worker→tester cycles)…")
        sid = ((api.api("session.create", body={"title": f"planner-{args.label}"}) or {}).get("data") or {}).get("id")
        prompt = build_task(args.task, args.label, args.cycles)
        api.prompt_async(sid, prompt, agent="planner")
        done = False
        last_report = 0
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            time.sleep(10)
            messages = api.context(sid)
            st = stats(messages)
            kids = api.children(sid)
            if time.time() - last_report > 30:
                last_report = time.time()
                print(f"[{args.label}] t+{int(time.time()-t0)}s msgs={st['messages']} kids={len(kids)} "
                      f"errs={len(st['errors'])} last={st['last'][:70]!r}")
            if st["last"] and "ALL CYCLES DONE" in st["last"].upper():
                done = True
                break
        report["planner"] = {
            "session": sid,
            "done": done,
            "duration_s": int(time.time() - t0),
            "parent": stats(api.context(sid)),
            "children": [],
        }
        notes = os.path.join(SCRATCH, f"notes-{args.label}.txt")
        report["notes_exists"] = os.path.exists(notes)
        report["notes_text"] = open(notes).read() if os.path.exists(notes) else ""
        tester_status = []
        for kid in api.children(sid):
            km = api.context(kid.get("id"))
            ks = stats(km)
            report["planner"]["children"].append({
                "id": kid.get("id"), "title": kid.get("title"), "agent": kid.get("agent"),
                **{k: v for k, v in ks.items() if k != "errors"},
                "errors": len(ks["errors"]),
            })
            for line in all_text(km).splitlines():
                if "STATUS:" in line.upper():
                    tester_status.append(line.strip()[:120])
        report["tester_status"] = tester_status

        spec_dir = os.path.join(SCRATCH, "docs", "superpowers", "specs")
        specs = sorted(os.listdir(spec_dir)) if os.path.isdir(spec_dir) else []
        report["spec_files"] = specs
        report["spec_text"] = open(os.path.join(spec_dir, specs[0])).read()[:2000] if specs else ""
        tester_pass = any("STATUS: PASS" in s.upper() for s in tester_status)
        child_agents = sorted({c.get("agent") for c in report["planner"]["children"] if c.get("agent")})
        report["child_agents"] = child_agents

        verify = TASKS[args.task]["verify"](args.label)
        report["verify"] = verify
        task_ok = TASKS[args.task]["success"](verify)
        if args.task == "mario":
            summary = (f"mario files={verify['files']} syntax={verify['js_syntax_ok']} "
                       f"refs={verify['html_refs']} mario={verify['has_mario']} match={verify['has_match']} "
                       f"score={verify['has_score']} moves={verify['has_moves']} restart={verify['has_restart']} "
                       f"readme={verify['readme_ok']}")
        else:
            summary = (f"notes={verify['notes_exists']} greet={verify['greet_exists']} "
                       f"notes_text={verify['notes_text']!r}")
        print(f"[{args.label}] planner done={done} kids={len(report['planner']['children'])} "
              f"agents={child_agents} spec={len(specs)} tester_pass={tester_pass} {summary}")
        report["success"] = bool(done and specs and tester_pass and task_ok)

        out = os.path.join(HERE, f"longtest-{args.label}.json")
        with open(out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"[{args.label}] report: {out}")
        print(f"[{args.label}] RESULT: {'PASS' if report['success'] else 'FAIL'}")
        return 0 if report["success"] else 1
    finally:
        try:
            os.killpg(os.getpgid(server.pid), signal.SIGTERM)
        except Exception:
            pass
        logf.close()


if __name__ == "__main__":
    sys.exit(main())
