#!/usr/bin/env python3
"""Live question-round E2E — real model, real OpenCode 2 server (handover §8.3).

The brainstormer must ask decision questions with REAL answer options — never
a yes/no flattening of an open decision, and (when the harness answers
options) the turn must keep working. Two acceptable outcomes:

  A) the brainstormer (or the planner relaying NEEDS_INPUT) calls the
     `question` tool — the test asserts the options are real alternatives
     (2-4 concrete choices, not a forced Yes/No pair) and that no question
     call failed on schema;
  B) the brainstormer proceeds on documented assumptions — the pipeline still
     completes (the question protocol is optional, an unanswered ambiguity
     must never block).

A forced Yes/No pair on an open decision is a FAIL (that is the exact
regression this suite guards: the old alias plugin invented Yes/No options).
This suite makes real model calls and takes minutes — NOT part of run_all.sh.

Run: python3 -u tests/e2e/test_question_live.py
Env: LT_PROVIDER (goproxy), PLANNER_V2_BIN, LT_QUESTION_TIMEOUT (default 600).
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

SCRATCH = "/tmp/planner-question-test"
HOME_DIR = "/tmp/planner-question-home"
LOG_PATH = "/tmp/planner-question-server.log"

PROVIDER = os.environ.get("LT_PROVIDER", "goproxy")
MODELS = {
    "planner": "deepseek-v4.1-flash",
    "brainstormer": "glm-5.3-flash",
    "worker": "deepseek-v4-flash",
    "tester": "mimo-v2.5",
}

NOTES_PY = '''#!/usr/bin/env python3
"""Tiny notes CLI: add and list notes stored in notes.json."""
import json, os, sys

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "notes.json")

def load():
    try:
        with open(DB) as f:
            return json.load(f)
    except Exception:
        return []

def save(items):
    with open(DB, "w") as f:
        json.dump(items, f, indent=2)

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    items = load()
    if cmd == "add" and len(sys.argv) > 2:
        items.append({"text": " ".join(sys.argv[2:]), "ts": __import__("time").time()})
        save(items)
        print("added")
    elif cmd == "list":
        for i, n in enumerate(items):
            print(f"{i}: {n['text']}")
    else:
        print("usage: notes.py add <text> | list")

if __name__ == "__main__":
    main()
'''

REQUEST = (
    "Add an export command to tools/notes.py. I have NOT decided the details yet — "
    "the export format, where the output goes, and how much to include are all open. "
    "Ask me about the decisions that actually matter (with concrete options to pick from) "
    "before you commit the spec."
)

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
    env["PLANNER_DEBUG"] = "1"
    env["PLANNER_DEBUG_FILE"] = "/tmp/planner-question-debug.log"
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


def write_config():
    cfg = {
        "models": {
            "planner": "",
            "brainstormer": f"{PROVIDER}/{MODELS['brainstormer']}",
            "worker": f"{PROVIDER}/{MODELS['worker']}",
            "tester": f"{PROVIDER}/{MODELS['tester']}",
        },
        "variants": {},
        "maxCycles": 3,
        "maxParallelSubagents": 1,
        "enforceDelegation": True,
        "vision": {"enabled": False, "model": "", "mode": "agent"},
    }
    os.makedirs(os.path.join(SCRATCH, ".opencode"), exist_ok=True)
    with open(os.path.join(SCRATCH, ".opencode", "planner-plugin.json"), "w") as f:
        json.dump(cfg, f, indent=2)


def setup_scratch():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    shutil.rmtree(HOME_DIR, ignore_errors=True)
    os.makedirs(HOME_DIR, exist_ok=True)
    pkg = os.path.join(SCRATCH, ".opencode", "plugins", "opencode-planner")
    os.makedirs(pkg)
    shutil.copytree(os.path.join(ROOT, "src"), os.path.join(pkg, "src"))
    shutil.copytree(os.path.join(ROOT, "agents-v2"), os.path.join(pkg, "agents-v2"))
    for f in ("index.ts", "tui.ts", "package.json"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(pkg, f))
    os.makedirs(os.path.join(SCRATCH, "tools"))
    with open(os.path.join(SCRATCH, "tools", "notes.py"), "w") as f:
        f.write(NOTES_PY)
    with open(os.path.join(SCRATCH, "README.md"), "w") as f:
        f.write("# notes tool\n")
    write_config()
    sh("git init -q -b main")
    sh("git config user.email t@t.com")
    sh("git config user.name t")
    sh("git add -A && git commit -qm init")


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

    def prompt_async(self, sid, text):
        cmd = [self.binary, "api", "session.prompt", "--server", f"http://127.0.0.1:{self.port}",
               "--param", f"sessionID={sid}", "-d", json.dumps({"text": text})]
        env = server_env()
        env["OPENCODE_PASSWORD"] = self.password
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=SCRATCH)

    def abort(self, sid):
        try:
            self.api("session.abort", {"sessionID": sid}, timeout=30)
        except Exception:
            pass


def db_path():
    return os.path.join(HOME_DIR, ".local", "share", "opencode", "opencode.db")


def scan_session_db():
    """Return (question_calls, needs_input_texts, children) from the scratch DB."""
    path = db_path()
    if not os.path.exists(path):
        return [], [], {}
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    questions, needs, children = [], [], {}
    try:
        for (data,) in db.execute("SELECT data FROM session_message WHERE type='assistant'"):
            try:
                content = json.loads(data).get("content") or []
            except Exception:
                continue
            for entry in content:
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "tool" and entry.get("name") in ("question", "ask_user"):
                    state = entry.get("state") or {}
                    questions.append({"status": state.get("status"), "input": state.get("input")})
                if entry.get("type") in ("text", "reasoning"):
                    text = entry.get("text") or ""
                    if "STATUS: NEEDS_INPUT" in text:
                        needs.append(text[:400])
        for r in db.execute("SELECT agent, COUNT(*) FROM session_v2 WHERE parent_id IS NOT NULL GROUP BY agent"):
            children[r[0] or "?"] = r[1]
    finally:
        db.close()
    return questions, needs, children


def options_of(qcall):
    qs = ((qcall or {}).get("input") or {}).get("questions") or []
    out = []
    for q in qs:
        if isinstance(q, dict):
            out.append({
                "question": q.get("question", ""),
                "header": q.get("header", ""),
                "options": [o.get("label") if isinstance(o, dict) else str(o) for o in (q.get("options") or [])],
            })
    return out


def main():
    binary = find_binary()
    if not binary:
        print("[SKIP] No OpenCode V2 binary found (set PLANNER_V2_BIN).")
        return 0
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

        sid = api.create("question-round")
        api.switch_planner(sid)
        proc = api.prompt_async(sid, REQUEST)
        check("prompt sent", proc is not None)

        timeout = int(os.environ.get("LT_QUESTION_TIMEOUT", "600"))
        deadline = time.time() + timeout
        question_calls, needs, children = [], [], {}
        while time.time() < deadline:
            time.sleep(10)
            question_calls, needs, children = scan_session_db()
            if question_calls:
                print(f"\n  INFO: question call detected after ~{int(time.time() - (deadline - timeout))}s")
                break
            if {"worker", "tester"}.issubset(children.keys()):
                break
            if proc.poll() is not None and not question_calls and {"worker", "tester"}.issubset(children.keys()):
                break

        # Give a just-started question a moment to settle, then re-read.
        time.sleep(5)
        question_calls, needs, children = scan_session_db()
        info("children", children)
        info("needs_input_texts", len(needs))
        info("question_calls", len(question_calls))

        pipeline_ran = {"worker", "tester"}.issubset(children.keys())
        check("brainstormer child session exists", "brainstormer" in children, str(children))

        if question_calls:
            print("\n[Question round happened]")
            q = question_calls[0]
            qs = options_of(q)
            info("question.status", q.get("status"))
            for entry in qs:
                info("asked", f"{entry['question'][:90]} options={entry['options']}")
            check("the question tool call carries questions", len(qs) >= 1, json.dumps(q)[:300])
            if qs:
                labels = qs[0]["options"]
                check("question has 2-4 options", 2 <= len(labels) <= 4, str(labels))
                forced_yes_no = [x.lower() for x in labels] in (
                    ["yes", "no"], ["no", "yes"], ["confirm", "decline"], ["true", "false"],
                ) and len(qs[0]["question"].split()) > 6  # a real question, not a binary one
                check("open decision is NOT flattened to yes/no", not forced_yes_no, str(labels))
                check("question header present and short",
                      0 < len(qs[0]["header"]) <= 30, repr(qs[0]["header"]))
            failed_calls = [c for c in question_calls if c.get("status") not in ("pending", "running", "completed")]
            check("no question call failed on schema/errors", not failed_calls,
                  json.dumps(failed_calls)[:300])
            print("\n  INFO: unanswered question — aborting the session (headless test cannot answer)")
            api.abort(sid)
        elif needs:
            print("\n[NEEDS_INPUT path] the brainstormer asked in its reply")
            check("NEEDS_INPUT produced by the brainstormer", True)
        else:
            print("\n[Assumptions path] no question was asked")
            check("pipeline ran without blocking on a question", pipeline_ran, str(children))

        if not question_calls and pipeline_ran:
            check("worker ran", "worker" in children, str(children))
            check("tester ran", "tester" in children, str(children))

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
