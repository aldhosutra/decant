#!/usr/bin/env python3
"""decant: pour a Claude Code session into a resumable OpenCode 2.x session.

    decant.py locate  <session-id-or-prefix> | --latest [--project DIR]
    decant.py prepare <session-id-or-prefix> | --latest [--project DIR] [options]
    decant.py import  <work-dir> --yes

`prepare` converts and proves the result in a throwaway OpenCode, with a fake local model.
`import` writes to the real OpenCode, and only for the exact file that passed.
Standard library only. Output is JSON on stdout; progress goes to stderr.
"""
import argparse
import base64
import collections
import copy
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TESTED_OPENCODE = "2.2"
DEFAULT_TAIL_CHARS = 200_000
DEFAULT_MAX_SUMMARY_CHARS = 240_000
REPLY_MARKER = "DECANT_REPLY_OK"
TOOL_MARKER = "DECANT_TOOL_OK"
ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

# Target names and arguments are the tools OpenCode 2.2 offers. `prepare` compares them with
# what the installed version really offers and reports any drift.
TOOL_MAP = {"Bash": "shell", "Read": "read", "Write": "write", "Edit": "edit",
            "WebSearch": "websearch", "WebFetch": "webfetch", "Agent": "subagent"}
ARG_MAP = {"Bash": {"run_in_background": "background"}, "Read": {"file_path": "path"},
           "Write": {"file_path": "path"}, "Agent": {"subagent_type": "agent"},
           "Edit": {"file_path": "path", "old_string": "oldString", "new_string": "newString",
                    "replace_all": "replaceAll"}}
ARG_DROP = {"Bash": {"description"}}


def log(msg):
    print(f"decant: {msg}", file=sys.stderr, flush=True)


def die(msg, code=2):
    print(f"decant: {msg}", file=sys.stderr)
    sys.exit(code)


def ms(ts):
    return int(dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)


# ---------------------------------------------------------------- locate

def config_roots():
    roots = []
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        roots.append(os.path.expanduser(os.environ["CLAUDE_CONFIG_DIR"]))
    roots += sorted(p for p in glob.glob(os.path.expanduser("~/.claude*")) if os.path.isdir(p))
    seen, out = set(), []
    for r in roots:
        real = os.path.realpath(r)
        if real not in seen and os.path.isdir(os.path.join(r, "projects")):
            seen.add(real)
            out.append(r)
    return out


def project_key(path):
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(os.path.expanduser(path)))


def locate(session=None, latest=False, project=None):
    """Return matching main transcripts, newest first. Subagent transcripts sit deeper and never match."""
    hits, seen = [], set()
    for root in config_roots():
        if latest or (project and not session):
            pattern = os.path.join(root, "projects", project_key(project or os.getcwd()), "*.jsonl")
        else:
            pattern = os.path.join(root, "projects", "*", f"{glob.escape(session)}*.jsonl")
        for p in glob.glob(pattern):
            real = os.path.realpath(p)
            if real not in seen and os.path.getsize(p) > 0:
                seen.add(real)
                st = os.stat(p)
                hits.append({"path": p, "session": os.path.basename(p)[:-6], "profile": root,
                             "bytes": st.st_size, "mtime": int(st.st_mtime)})
    hits.sort(key=lambda h: -h["mtime"])
    return hits


# ---------------------------------------------------------------- convert

class Ids:
    """OpenCode ids: prefix + 12 hex of (ms*4096+counter) + 14 chars. Messages ascend, sessions descend."""

    def __init__(self, seed):
        self.seed, self.last, self.n = seed, 0, 0

    def make(self, prefix, t, descending=False):
        self.last = max(self.last, t)
        self.n += 1
        v = (self.last * 0x1000 + self.n % 0x1000) & ((1 << 48) - 1)
        if self.n % 0x1000 == 0:
            self.last += 1
        if descending:
            v = ~v & ((1 << 48) - 1)
        tail = "".join(ALNUM[b % 62] for b in hashlib.sha256(f"{self.seed}:{self.n}".encode()).digest()[:14])
        return f"{prefix}_{v:012x}{tail}"


def read_records(path, stats):
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                stats["malformed_lines_skipped"] += 1
    return recs


def active_path(recs):
    """Walk parentUuid from the newest main-thread record. A compact boundary is a root: its
    logicalParentUuid can form a cycle, so it is never followed."""
    by = {r["uuid"]: r for r in recs if r.get("uuid")}
    mains = [r for r in recs if r.get("uuid") and r.get("type") in ("user", "assistant") and not r.get("isSidechain")]
    if not mains:
        die("this transcript has no conversation to convert", 1)
    path, seen, cur = [], set(), mains[-1]
    while cur and cur["uuid"] not in seen:
        seen.add(cur["uuid"])
        path.append(cur)
        cur = by.get(cur.get("parentUuid"))
    path.reverse()
    return path, seen


def result_text(block, stats):
    c = block.get("content")
    if isinstance(c, str):
        return c
    out = []
    for p in c or []:
        kind = p.get("type")
        if kind == "text":
            out.append(p.get("text", ""))
        elif kind == "image":
            stats["images_replaced"] += 1
            raw = p.get("source", {}).get("data", "") or ""
            out.append(f"[image omitted by decant: {len(base64.b64decode(raw, validate=False))} bytes]")
        else:
            stats[f"result_blocks_replaced:{kind}"] += 1
            out.append(f"[{kind} omitted by decant]")
    return "\n".join(out)


def user_text(content, stats):
    if isinstance(content, str):
        return content
    out = []
    for p in content:
        if p.get("type") == "text":
            out.append(p.get("text", ""))
        elif p.get("type") == "image":
            stats["images_replaced"] += 1
            out.append("[image omitted by decant]")
    return "\n".join(out)


def tool_part(b, when, results, stats, files):
    name = b["name"]
    remap, drop = ARG_MAP.get(name, {}), ARG_DROP.get(name, set())
    args = {remap.get(k, k): v for k, v in (b.get("input") or {}).items() if k not in drop}
    got = results.get(b["id"])
    done = ms(got[1]) if got else when
    part = {"type": "tool", "id": b["id"], "name": TOOL_MAP.get(name, name), "time": {"created": when, "completed": done}}
    stats["tool_calls"] += 1
    stats[f"tool:{name}"] += 1
    if got is None:
        stats["tool_calls_unanswered"] += 1
        part["state"] = {"status": "error", "input": args,
                         "error": {"type": "unknown", "message": "No result in the source transcript"}}
        return part, done
    text = result_text(got[0], stats)
    if got[0].get("is_error"):
        stats["tool_results_error"] += 1
        part["state"] = {"status": "error", "input": args, "error": {"type": "unknown", "message": text or "Tool failed"}}
        return part, done
    stats["tool_results_ok"] += 1
    meta = {"truncated": False}
    if name == "Bash":
        meta.update(output=text, exit=0)
    elif name == "Read":
        meta.update(preview=text[:1500], loaded=[])
    part["state"] = {"status": "completed", "input": args, "content": [{"type": "text", "text": text}], "metadata": meta}
    if name in ("Edit", "Write") and args.get("path") and args["path"] not in files:
        files.append(args["path"])
    return part, done


def build_summary(transcript, summary, files, digest, tail_texts, limit, stats):
    head = ["This session was imported from Claude Code by decant. Below is the conversation so far: the earlier "
            "summary, then the operator's messages and the assistant's visible replies verbatim, with tool activity "
            f"omitted. The original transcript with all tool output is at {os.path.abspath(transcript)}.",
            "\n# Summary from before this session\n" + (summary or "(none)"),
            "\n# Files edited or written in this session\n" + ("\n".join(files) or "(none)"),
            "\n# Conversation"]
    entries = [f"\n## {role}\n{text}" for role, text in digest if text not in tail_texts]
    budget, kept = limit - sum(len(h) + 1 for h in head), []
    for e in reversed(entries):  # the newest context matters most when something must give
        if budget - len(e) - 1 < 0:
            break
        budget -= len(e) + 1
        kept.append(e)
    kept.reverse()
    omitted = len(entries) - len(kept)
    if omitted:
        stats["summary_entries_omitted"] = omitted
        head.append(f"\n({omitted} older entries omitted for length; see the original transcript.)")
    return "\n".join(head + kept)


def convert(transcript, directory, project_id, title=None, salt="",
            tail_chars=DEFAULT_TAIL_CHARS, max_summary_chars=DEFAULT_MAX_SUMMARY_CHARS):
    stats = collections.Counter()
    recs = read_records(transcript, stats)
    src = next((r["sessionId"] for r in recs if r.get("sessionId")), os.path.basename(transcript)[:-6])
    path, on_path = active_path(recs)
    stats["offpath_records_dropped"] = sum(
        1 for r in recs if r.get("uuid") and r.get("type") in ("user", "assistant")
        and r["uuid"] not in on_path and not r.get("isSidechain"))
    stats["sidechain_records_dropped"] = sum(1 for r in recs if r.get("isSidechain") and r.get("type") in ("user", "assistant"))

    # A tool result can sit on a sibling branch of its call when calls ran in parallel.
    results = {}
    for r in recs:
        if r.get("type") == "user" and isinstance(r.get("message", {}).get("content"), list):
            for p in r["message"]["content"]:
                if p.get("type") == "tool_result":
                    results.setdefault(p["tool_use_id"], (p, r["timestamp"]))

    ids = Ids(src + salt)
    msgs, digest, files, summary = [], [], [], None
    current, current_key = None, None

    def add_user(text, when):
        nonlocal current, current_key
        current, current_key = None, None
        msgs.append({"id": ids.make("msg", when), "time": {"created": when}, "text": text, "type": "user"})
        digest.append(("user", text))

    for r in path:
        kind = r.get("type")
        if kind == "attachment":
            a = r["attachment"]
            text = a.get("prompt") or a.get("content") if a.get("type") == "queued_command" else None
            if isinstance(text, list):
                text = user_text(text, stats)
            if isinstance(text, str) and text.strip():
                add_user(text, ms(r["timestamp"]))  # typed while the agent was working: a real operator message
                stats["queued_user_messages_kept"] += 1
            else:
                stats["attachments_dropped"] += 1
            continue
        if kind == "system":
            stats["system_records_dropped"] += 1
            continue
        when = ms(r["timestamp"])
        if kind == "user":
            content = r["message"]["content"]
            if r.get("isCompactSummary"):
                summary = user_text(content, stats)
                current, current_key = None, None
                msgs.append({"id": ids.make("msg", when), "time": {"created": when}, "type": "compaction",
                             "status": "completed", "reason": "auto", "summary": summary})
                stats["source_compactions_kept"] += 1
            elif isinstance(content, list) and content and all(p.get("type") == "tool_result" for p in content):
                pass  # attached to its call
            elif r.get("isMeta"):
                stats["meta_messages_dropped"] += 1
            else:
                text = user_text(content, stats)
                if text.strip():
                    add_user(text, when)
                    stats["user_messages_kept"] += 1
            continue
        if kind != "assistant":
            continue
        m = r["message"]
        if m.get("model") == "<synthetic>" or r.get("isApiErrorMessage"):
            stats["api_error_records_dropped"] += 1
            continue
        key = m.get("id") or r["uuid"]  # one model response is streamed as several records
        if key != current_key:
            current = {"id": ids.make("msg", when), "time": {"created": when, "completed": when}, "type": "assistant",
                       "agent": "build",
                       "model": {"id": m.get("model") or "unknown", "providerID": "anthropic", "variant": "default"},
                       "content": [], "finish": "stop", "cost": 0,
                       "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}}
            msgs.append(current)
            current_key = key
            stats["assistant_messages"] += 1
        for b in m.get("content") or []:
            t = b.get("type")
            if t == "text" and b.get("text", "").strip():
                current["content"].append({"type": "text", "text": b["text"]})
                digest.append(("assistant", b["text"]))
                stats["assistant_text_blocks"] += 1
            elif t == "thinking":
                if b.get("thinking", "").strip():
                    current["content"].append({"type": "reasoning", "text": b["thinking"], "time": {"created": when, "completed": when}})
                    stats["reasoning_blocks_kept"] += 1
                else:
                    stats["thinking_blocks_empty_dropped"] += 1
            elif t == "tool_use":
                part, done = tool_part(b, when, results, stats, files)
                current["content"].append(part)
                current["time"]["completed"] = max(current["time"]["completed"], done)

    msgs = [m for m in msgs if m["type"] != "assistant" or m["content"]]
    if not msgs:
        die("nothing convertible was found in this transcript", 1)

    # Only messages after the newest compaction are replayed to the model. Keep that window small.
    last = max((i for i, m in enumerate(msgs) if m["type"] == "compaction"), default=-1)
    cut, size = None, 0
    if sum(len(json.dumps(m)) for m in msgs[last + 1:]) > tail_chars:
        for i in range(len(msgs) - 1, last, -1):
            size += len(json.dumps(msgs[i]))
            if msgs[i]["type"] == "user":
                if size > tail_chars and cut is not None:
                    break
                cut = i
    if cut is None:
        replay_start = last + 1
    else:
        replay_start = cut

    # A Claude thinking signature cannot be carried over, so thinking must never be replayed.
    # It stays readable in the history before the replay window.
    replay = msgs[replay_start:]
    for m in replay:
        if m["type"] == "assistant":
            kept = [p for p in m["content"] if p["type"] != "reasoning"]
            stats["reasoning_removed_from_replay"] += len(m["content"]) - len(kept)
            m["content"] = kept
    replay = [m for m in replay if m["type"] != "assistant" or m["content"]]
    msgs = [m for m in msgs[:replay_start] if m["type"] != "assistant" or m["content"]] + replay

    if cut is not None:
        tail_texts = set()
        for m in replay:
            if m["type"] == "user":
                tail_texts.add(m["text"])
            elif m["type"] == "assistant":
                tail_texts.update(p["text"] for p in m["content"] if p["type"] == "text")
        text = build_summary(transcript, summary, files, digest, tail_texts, max_summary_chars, stats)
        marker = {"id": "", "time": {"created": replay[0]["time"]["created"]}, "type": "compaction",
                  "status": "completed", "reason": "manual", "summary": text}
        msgs.insert(len(msgs) - len(replay), marker)
        stats["decant_compaction_added"] = 1
        stats["compaction_summary_chars"] = len(text)
    stats["messages_replayed"] = len(replay)
    stats["replay_chars"] = sum(len(json.dumps(m)) for m in replay)
    if cut is None and any(m["type"] == "compaction" for m in msgs) is False and stats["replay_chars"] > tail_chars:
        stats["replay_window_oversized"] = 1

    ids = Ids(src + salt)  # final order is known only now, and ids must ascend in it
    for m in msgs:
        m["id"] = ids.make("msg", m["time"]["created"])

    created = msgs[0]["time"]["created"]
    updated = msgs[-1]["time"].get("completed", msgs[-1]["time"]["created"])
    titles = [r["aiTitle"] for r in recs if r.get("type") == "ai-title" and r.get("aiTitle")]
    session = {"info": {"id": Ids("session:" + src + salt).make("ses", created, descending=True),
                        "projectID": project_id, "agent": "build",
                        "model": {"id": msgs_model(msgs), "providerID": "anthropic", "variant": "default"},
                        "cost": 0, "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                        "time": {"created": created, "updated": updated},
                        "title": title or (titles[-1] if titles else "Imported Claude Code session"),
                        "location": {"directory": directory}},
               "messages": msgs}
    stats["messages_total"] = len(msgs)
    return session, dict(sorted(stats.items())), src


def msgs_model(msgs):
    for m in reversed(msgs):
        if m["type"] == "assistant":
            return m["model"]["id"]
    return "unknown"


def source_directory(transcript):
    counts = collections.Counter()
    with open(transcript, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("cwd") and not r.get("isSidechain"):
                counts[r["cwd"]] += 1
    for path, _ in counts.most_common():
        if os.path.isdir(path):
            return path
    return None


def project_id_for(directory):
    """OpenCode caches the id in <git-common-dir>/opencode, else derives it from the first root commit."""
    def git(*args):
        r = subprocess.run(["git", "-C", directory, *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    common = git("rev-parse", "--git-common-dir")
    if not common:
        return "global"
    cached = os.path.join(directory, common, "opencode")
    if os.path.isfile(cached):
        value = open(cached, encoding="utf-8").read().strip()
        if value:
            return value
    roots = sorted(git("rev-list", "--max-parents=0", "HEAD").split())
    return roots[0] if roots else "global"


# ---------------------------------------------------------------- verify

def comparable(msgs):
    out = []
    for m in msgs:
        if m["type"] == "user":
            out.append(("user", m["text"]))
        elif m["type"] == "compaction":
            out.append(("compaction", m["status"], m.get("summary")))
        elif m["type"] == "assistant":
            for p in m["content"]:
                if p["type"] == "tool":
                    s = p["state"]
                    out.append(("tool", p["id"], p["name"], s["status"], json.dumps(s["input"], sort_keys=True),
                                json.dumps(s.get("content"), sort_keys=True), json.dumps(s.get("error"), sort_keys=True)))
                else:
                    out.append((p["type"], p["text"]))
    return out


def start_mock():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"object":"list","data":[{"id":"m","object":"model"}]}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            requests.append(body)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()

            def chunk(delta, finish=None):
                d = {"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "m",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(d)}\n\n".encode())
                self.wfile.flush()

            messages = body.get("messages", [])
            done = any(m.get("role") == "tool" and TOOL_MARKER in json.dumps(m.get("content")) for m in messages)
            tool = next((t["function"] for t in body.get("tools") or [] if t["function"]["name"] in ("shell", "bash")), None)
            if tool and not done:
                args = {"command": f"echo {TOOL_MARKER}"}
                for req in tool["parameters"].get("required", []):
                    args.setdefault(req, "decant check")
                chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_decant", "type": "function",
                       "function": {"name": tool["name"], "arguments": json.dumps(args)}}]})
                chunk({}, "tool_calls")
            else:
                chunk({"role": "assistant", "content": REPLY_MARKER})
                chunk({}, "stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, requests


def opencode_version():
    r = subprocess.run(["opencode", "--version"], capture_output=True, text=True)
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", r.stdout + r.stderr)
    return m.group(0) if m else None


def analyse_replay(session, requests, checks, advisories, report):
    if not requests:
        checks["model_received_request"] = False
        return
    checks["model_received_request"] = True
    body = requests[0]
    messages = body["messages"]
    text = json.dumps(messages)
    calls = [t for m in messages for t in (m.get("tool_calls") or [])]
    answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    checks["tool_calls_paired_with_results"] = not ({c["id"] for c in calls} - answered)
    checks["thinking_not_replayed"] = "reasoning_content" not in text and not any("reasoning" in m for m in messages)

    msgs = session["messages"]
    last = max((i for i, m in enumerate(msgs) if m["type"] == "compaction"), default=-1)
    if last >= 0:
        checks["compaction_summary_sent"] = json.dumps(msgs[last]["summary"][:80])[1:-1] in text
    # Compare real call ids. A plain text search would also match ids merely quoted in tool output.
    sent = {c["id"] for c in calls}
    tail = {p["id"] for m in msgs[last + 1:] if m["type"] == "assistant" for p in m["content"] if p["type"] == "tool"}
    checks["replay_window_complete"] = tail <= sent
    before = {p["id"] for m in msgs[:max(last, 0)] if m["type"] == "assistant" for p in m["content"] if p["type"] == "tool"}
    checks["older_history_not_replayed"] = not (before - tail) & sent

    offered = {t["function"]["name"]: set(t["function"]["parameters"].get("properties", {})) for t in body.get("tools") or []}
    unknown, bad_args = collections.Counter(), collections.Counter()
    for c in calls:
        name = c["function"]["name"]
        if name not in offered:
            unknown[name] += 1
            continue
        for arg in json.loads(c["function"]["arguments"] or "{}"):
            if arg not in offered[name]:
                bad_args[f"{name}.{arg}"] += 1
    if unknown:
        advisories.append({"code": "tools_not_offered", "detail": dict(unknown),
                           "meaning": "Old calls to tools OpenCode does not have. Harmless history unless a mapped core tool appears here."})
    drift = sorted(set(TOOL_MAP.values()) - set(offered))
    if drift:
        advisories.append({"code": "tool_map_drift", "detail": drift,
                           "meaning": "This OpenCode does not offer these mapped tools; update TOOL_MAP before relying on it."})
    if bad_args:
        advisories.append({"code": "arguments_not_offered", "detail": dict(bad_args),
                           "meaning": "Old call arguments the current tool schema lacks. A model copying them may make an invalid call."})
    tokens = len(text) // 4
    report["replay"] = {"approx_tokens": tokens, "messages": len(messages), "tool_calls": len(calls)}
    if tokens > 150_000:
        advisories.append({"code": "large_replay", "detail": tokens,
                           "meaning": "Resuming may overflow smaller models. Re-run prepare with a lower --tail-chars or --max-summary-chars."})


def check_tui(env_file, work, sid, checks):
    if not shutil.which("tmux"):
        return "skipped: tmux is not installed"
    sock = f"decant-{os.getpid()}"
    cmd = f". {shlex.quote(env_file)}; cd {shlex.quote(work)}; opencode --standalone -s {sid}; sleep 5"
    try:
        subprocess.run(["tmux", "-L", sock, "new-session", "-d", "-s", "t", "-x", "130", "-y", "34", cmd], check=True, capture_output=True)
        shown = False
        for _ in range(15):
            time.sleep(2)
            out = subprocess.run(["tmux", "-L", sock, "capture-pane", "-p", "-t", "t"], capture_output=True, text=True).stdout
            if REPLY_MARKER in out:
                shown = True
                break
        checks["interface_renders_session"] = shown
        return "ran"
    finally:
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True)


def verify(session):
    """Prove a session in a throwaway OpenCode. Every path is private, and it uses an empty workspace,
    so the real store is never touched and the project's own plugins and MCP servers never start."""
    version = opencode_version()
    report = {"opencode_version": version, "tested_with": TESTED_OPENCODE, "checks": {}, "advisories": []}
    checks, advisories = report["checks"], report["advisories"]
    if not version or not version.startswith("2."):
        report["pass"] = False
        report["error"] = f"OpenCode 2.x is required, found {version}"
        return report
    if not version.startswith(TESTED_OPENCODE + ".") and version != TESTED_OPENCODE:
        advisories.append({"code": "untested_version", "detail": version,
                           "meaning": f"Decant was developed against {TESTED_OPENCODE}.x. Passing checks below still apply to this version."})
    iso = tempfile.mkdtemp(prefix="decant-verify-")
    os.chmod(iso, 0o700)
    server, requests = start_mock()
    try:
        env = {k: v for k, v in os.environ.items() if not k.startswith("OPENCODE")}
        paths = {"HOME": "home", "XDG_DATA_HOME": "data", "XDG_CONFIG_HOME": "config", "XDG_STATE_HOME": "state", "XDG_CACHE_HOME": "cache"}
        for key, name in paths.items():
            env[key] = os.path.join(iso, name)
            os.makedirs(env[key])
        work = os.path.join(iso, "work")
        os.makedirs(work)
        os.makedirs(os.path.join(env["XDG_CONFIG_HOME"], "opencode"))
        base = f"http://127.0.0.1:{server.server_address[1]}/v1"
        with open(os.path.join(env["XDG_CONFIG_HOME"], "opencode", "opencode.json"), "w") as f:
            json.dump({"model": "mock/m", "small_model": "mock/m", "provider": {"mock": {
                "npm": "@ai-sdk/openai-compatible", "name": "Mock", "options": {"baseURL": base, "apiKey": "unused"},
                "models": {"m": {"name": "m", "tool_call": True, "limit": {"context": 1_000_000, "output": 8000}}}}}}, f)
        env_file = os.path.join(iso, "env.sh")
        with open(env_file, "w") as f:
            f.writelines(f"export {k}={shlex.quote(env[k])}\n" for k in paths)
            f.write("".join(f"unset {k}\n" for k in os.environ if k.startswith("OPENCODE")))

        trial = copy.deepcopy(session)
        trial["info"]["projectID"] = "global"
        trial["info"]["location"]["directory"] = work
        trial_file = os.path.join(iso, "session.json")
        with open(trial_file, "w", encoding="utf-8") as f:
            json.dump(trial, f, ensure_ascii=False)
        sid = trial["info"]["id"]

        def oc(*args, timeout=180):
            return subprocess.run(["opencode", *args], cwd=work, env=env, capture_output=True, text=True, timeout=timeout)

        log("importing into a throwaway OpenCode")
        r = oc("session", "import", "--standalone", "--directory", work, trial_file)
        checks["import_accepted"] = r.returncode == 0 and sid in r.stdout + r.stderr
        if not checks["import_accepted"]:
            report["error"] = (r.stdout + r.stderr)[-1500:]
            report["pass"] = False
            return report
        checks["isolated_store_used"] = os.path.exists(os.path.join(env["XDG_DATA_HOME"], "opencode", "opencode.db"))

        r = oc("session", "export", "--standalone", sid)
        try:
            back = json.loads(r.stdout)
            checks["round_trip_identical"] = comparable(trial["messages"]) == comparable(back["messages"])
        except (json.JSONDecodeError, KeyError):
            checks["round_trip_identical"] = False

        r = oc("session", "list", "--standalone")
        checks["listed_in_picker"] = sid in r.stdout

        log("resuming against a fake local model")
        r = oc("run", "--standalone", "--auto", "-s", sid, "-m", "mock/m", "Reply with OK")
        checks["resumed_turn_completed"] = r.returncode == 0 and REPLY_MARKER in r.stdout
        checks["new_tool_call_ran"] = any(m.get("role") == "tool" and TOOL_MARKER in json.dumps(m.get("content")) for q in requests for m in q.get("messages", []))
        if not checks["resumed_turn_completed"]:
            report["run_error"] = r.stderr[-1500:]
        analyse_replay(trial, requests, checks, advisories, report)
        log("opening the OpenCode interface")
        report["interface"] = check_tui(env_file, work, sid, checks)
        report["pass"] = all(checks.values())
        return report
    finally:
        server.shutdown()
        shutil.rmtree(iso, ignore_errors=True)


# ---------------------------------------------------------------- commands

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def pick(args):
    if not args.session and not args.latest:
        die("give a session id or prefix, or --latest")
    hits = locate(args.session, args.latest, args.project)
    if not hits:
        die("no matching Claude Code session; searched: " + ", ".join(config_roots()), 1)
    return hits


def cmd_locate(args):
    print(json.dumps({"matches": pick(args)[:20]}, indent=1))


def cmd_prepare(args):
    hits = pick(args)
    if not args.latest and len({h["session"] for h in hits}) > 1:
        print(json.dumps({"ambiguous": hits[:20]}, indent=1))
        die("that prefix matches several sessions; use a longer one", 1)
    source = hits[0]
    directory = args.directory or source_directory(source["path"])
    if not directory or not os.path.isdir(directory):
        die("the session's project folder no longer exists; pass --directory", 1)
    directory = os.path.abspath(directory)
    project = project_id_for(directory)
    session, stats, src = convert(source["path"], directory, project, args.title, args.salt, args.tail_chars, args.max_summary_chars)
    work = args.out_dir or os.path.expanduser(f"~/.cache/decant/{src}")
    os.makedirs(work, mode=0o700, exist_ok=True)
    file = os.path.join(work, "session.json")
    write_private(file, json.dumps(session, ensure_ascii=False))
    report = verify(session)
    report["sha256"] = sha256(file)
    write_private(os.path.join(work, "verify.json"), json.dumps(report, indent=1))
    manifest = {"source_session": src, "source_transcript": source["path"], "profile": source["profile"],
                "session_id": session["info"]["id"], "title": session["info"]["title"], "directory": directory,
                "project_id": project, "stats": stats}
    write_private(os.path.join(work, "manifest.json"), json.dumps(manifest, indent=1))
    print(json.dumps({"work_dir": work, "pass": report["pass"], "manifest": manifest, "verify": report}, indent=1))
    sys.exit(0 if report["pass"] else 1)


def cmd_import(args):
    work = os.path.abspath(os.path.expanduser(args.work_dir))
    file = os.path.join(work, "session.json")
    try:
        manifest = json.load(open(os.path.join(work, "manifest.json")))
        report = json.load(open(os.path.join(work, "verify.json")))
    except FileNotFoundError:
        die("run prepare first: this folder has no manifest or verification report", 1)
    if not os.path.exists(file):
        die("session.json is gone; this session was probably imported already, or run prepare again", 1)
    if not report.get("pass"):
        die("refusing: this conversion did not pass verification", 1)
    if report.get("sha256") != sha256(file):
        die("refusing: session.json changed after it was verified; run prepare again", 1)
    if not args.yes:
        die("importing adds a session to the real OpenCode; re-run with --yes once the user has agreed")
    sid = manifest["session_id"]
    directory = manifest["directory"]
    if subprocess.run(["opencode", "session", "export", sid], capture_output=True, cwd=directory).returncode == 0:
        die(f"refusing: {sid} already exists; re-run prepare with --salt to make a separate copy", 1)
    r = subprocess.run(["opencode", "session", "import", "--directory", directory, file], capture_output=True, text=True, cwd=directory)
    if r.returncode != 0 or sid not in r.stdout + r.stderr:
        die("OpenCode rejected the import: " + (r.stdout + r.stderr)[-1500:], 1)
    expected = len(json.load(open(file, encoding="utf-8"))["messages"])
    back = subprocess.run(["opencode", "session", "export", sid], capture_output=True, text=True, cwd=directory)
    try:
        got = len(json.loads(back.stdout)["messages"])
    except (json.JSONDecodeError, KeyError):
        got = -1
    listed = sid in subprocess.run(["opencode", "session", "list"], capture_output=True, text=True, cwd=directory).stdout
    ok = got == expected
    if ok:
        os.remove(file)  # holds the whole transcript in plain text; OpenCode has its own copy now
    print(json.dumps({"imported": ok, "session_id": sid, "messages": {"expected": expected, "found": got}, "listed_in_project": listed,
                      "resume": f"cd {shlex.quote(directory)} && opencode -s {sid}"}, indent=1))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def target(p):
        p.add_argument("session", nargs="?", help="session id or prefix")
        p.add_argument("--latest", action="store_true", help="newest session of the project")
        p.add_argument("--project", help="project folder for --latest (default: current folder)")

    p = sub.add_parser("locate")
    target(p)
    p.set_defaults(fn=cmd_locate)
    p = sub.add_parser("prepare")
    target(p)
    p.add_argument("--directory", help="folder to attach the session to (default: where it mostly ran)")
    p.add_argument("--title")
    p.add_argument("--salt", default="", help="any text; makes a separate copy with new ids")
    p.add_argument("--tail-chars", type=int, default=DEFAULT_TAIL_CHARS, help="recent history replayed verbatim")
    p.add_argument("--max-summary-chars", type=int, default=DEFAULT_MAX_SUMMARY_CHARS)
    p.add_argument("--out-dir", help="default: ~/.cache/decant/<session>")
    p.set_defaults(fn=cmd_prepare)
    p = sub.add_parser("import")
    p.add_argument("work_dir")
    p.add_argument("--yes", action="store_true", help="the user has agreed to add this session to OpenCode")
    p.set_defaults(fn=cmd_import)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
