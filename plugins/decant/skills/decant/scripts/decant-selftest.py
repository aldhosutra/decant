#!/usr/bin/env python3
"""Offline checks for decant's conversion, on a synthetic transcript. Needs no OpenCode.

    decant-selftest.py              conversion checks only
    decant-selftest.py --opencode   also proves the synthetic session in a throwaway OpenCode
"""
import importlib.util
import json
import os
import sys
import tempfile

spec = importlib.util.spec_from_file_location("decant", os.path.join(os.path.dirname(os.path.abspath(__file__)), "decant.py"))
decant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decant)

SID = "00000000-0000-4000-8000-000000000001"
clock = 0


def stamp():
    global clock
    clock += 1
    return f"2026-01-01T00:{clock // 60:02d}:{clock % 60:02d}.000Z"


def rec(kind, uuid, parent, **extra):
    return {"type": kind, "uuid": uuid, "parentUuid": parent, "timestamp": stamp(), "sessionId": SID, "isSidechain": False, "cwd": "/tmp", **extra}


def user(uuid, parent, content, **extra):
    return rec("user", uuid, parent, message={"role": "user", "content": content}, **extra)


def assistant(uuid, parent, mid, content, model="claude-opus-5-5", **extra):
    return rec("assistant", uuid, parent, message={"id": mid, "role": "assistant", "model": model, "content": content}, **extra)


def tool_use(i, name="Bash", **inp):
    return {"type": "tool_use", "id": i, "name": name, "input": inp}


def tool_result(i, content="ok", **extra):
    return {"type": "tool_result", "tool_use_id": i, "content": content, **extra}


def transcript(padding=0):
    pad = "x" * padding
    return [
        rec("system", "b", None, subtype="compact_boundary", logicalParentUuid="b"),  # cycle on purpose
        user("s", "b", "EARLIER SUMMARY", isCompactSummary=True),
        user("u1", "s", "first request"),
        assistant("a1", "u1", "m1", [{"type": "thinking", "thinking": "private thought", "signature": "sig"}]),
        assistant("a2", "a1", "m1", [{"type": "text", "text": "first reply"}]),
        assistant("a3", "a2", "m1", [tool_use("t1", command="echo hi", description="drop me")]),
        user("r1", "a3", [tool_result("t1", pad)]),
        assistant("a4", "r1", "m2", [tool_use("t2", "Read", file_path="/tmp/a"), tool_use("t3", "Edit", file_path="/tmp/b", old_string="o", new_string="n")]),
        user("r2", "a4", [tool_result("t2", [{"type": "image", "source": {"data": "aGVsbG8="}}])]),
        user("r3-sibling", "a4", [tool_result("t3", "edited")]),  # parallel result on a sibling branch
        assistant("abandoned", "r2", "m3", [{"type": "text", "text": "ABANDONED BRANCH"}]),
        rec("attachment", "q1", "r2", attachment={"type": "queued_command", "prompt": "typed while busy"}),
        assistant("err", "q1", "m4", [{"type": "text", "text": "API ERROR"}], model="<synthetic>", isApiErrorMessage=True),
        user("meta", "err", "skill body", isMeta=True),
        user("u2", "meta", "second request"),
        assistant("a5", "u2", "m5", [{"type": "thinking", "thinking": "recent thought", "signature": "sig"}, {"type": "text", "text": "final reply"}, tool_use("t4", command="never answered")]),
        {**assistant("side", "a5", "m9", [{"type": "text", "text": "SUBAGENT TEXT"}]), "isSidechain": True},
    ]


def run(records, **kw):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, f"{SID}.jsonl")
        with open(path, "w") as f:
            f.write("{not json\n")
            for r in records:
                f.write(json.dumps(r) + "\n")
        first = decant.convert(path, "/tmp", "global", **kw)
        again = decant.convert(path, "/tmp", "global", **kw)
    return first, json.dumps(first[0]) == json.dumps(again[0])


fails = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


(session, stats, _), stable = run(transcript())
msgs = session["messages"]
blob = json.dumps(msgs)
tools = {p["id"]: p for m in msgs if m["type"] == "assistant" for p in m["content"] if p["type"] == "tool"}
check("deterministic", stable)
check("malformed line skipped", stats.get("malformed_lines_skipped") == 1)
check("compaction cycle terminates and summary kept", msgs[0]["type"] == "compaction" and msgs[0]["summary"] == "EARLIER SUMMARY")
check("streamed records merge into one message", sum(1 for m in msgs if m["type"] == "assistant" and "first reply" in json.dumps(m)) == 1)
check("Bash maps to shell without description", tools["t1"]["name"] == "shell" and tools["t1"]["state"]["input"] == {"command": "echo hi"})
check("Read and Edit use path", tools["t2"]["state"]["input"] == {"path": "/tmp/a"} and tools["t3"]["state"]["input"]["path"] == "/tmp/b")
check("sibling-branch tool result recovered", tools["t3"]["state"]["status"] == "completed")
check("image replaced by placeholder", "image omitted by decant: 5 bytes" in blob and stats["images_replaced"] == 1)
check("unanswered tool call closed as error", tools["t4"]["state"]["status"] == "error")
check("queued message kept", "typed while busy" in blob)
check("abandoned branch dropped", "ABANDONED BRANCH" not in blob)
check("api error dropped", "API ERROR" not in blob)
check("meta message dropped", "skill body" not in blob)
check("sidechain ignored for leaf and content", "SUBAGENT TEXT" not in blob and "final reply" in blob)
check("thinking removed from replay", "recent thought" not in blob and "private thought" not in blob)
check("small session gets no added compaction", sum(m["type"] == "compaction" for m in msgs) == 1)
ids = [m["id"] for m in msgs]
check("message ids strictly ascend", ids == sorted(ids) and len(set(ids)) == len(ids))

(big, big_stats, _), _ = run(transcript(padding=50_000), tail_chars=1_000)
kinds = [m["type"] for m in big["messages"]]
check("oversized session gets a tail compaction", kinds.count("compaction") == 2 and big_stats.get("decant_compaction_added") == 1)
last = max(i for i, k in enumerate(kinds) if k == "compaction")
check("compaction precedes a user turn", kinds[last + 1] == "user")
summary = big["messages"][last]["summary"]
check("summary carries prior conversation", "EARLIER SUMMARY" in summary and "first request" in summary and "first reply" in summary)
check("older thinking stays readable", "private thought" in json.dumps(big["messages"][:last]))
(salted, _, _), _ = run(transcript(), salt="copy2")
check("salt gives new session id", salted["info"]["id"] != session["info"]["id"])
(capped, capped_stats, _), _ = run(transcript(padding=50_000), tail_chars=1_000, max_summary_chars=300)
check("summary length cap reports omissions", capped_stats.get("summary_entries_omitted", 0) >= 1)

if "--opencode" in sys.argv:
    report = decant.verify(session)
    print(json.dumps(report, indent=1))
    check("proved in a throwaway OpenCode", bool(report.get("pass")))

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
