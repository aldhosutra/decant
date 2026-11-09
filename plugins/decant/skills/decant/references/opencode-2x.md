# OpenCode 2.x target format

Measured on OpenCode 2.2.6 from real exports and the schema strings in its binary. OpenCode publishes no format spec, so treat a real export as the authority.

## Session file

```json
{"info": {"id": "ses_...", "projectID": "...", "agent": "build",
          "model": {"id": "...", "providerID": "...", "variant": "default"},
          "cost": 0, "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
          "time": {"created": 0, "updated": 0}, "title": "...", "location": {"directory": "..."}},
 "messages": []}
```

## Message types

| `type` | Fields |
|---|---|
| `user` | `id`, `time.created`, `text` |
| `assistant` | `id`, `time.created/completed`, `agent`, `model`, `content[]`, `finish`, `cost`, `tokens` |
| `compaction` | `id`, `time.created`, `status: "completed"`, `reason: "auto" or "manual"`, `summary` |

Assistant `content[]` items are `text`, `reasoning`, or `tool`:

```json
{"type": "tool", "id": "...", "name": "shell", "time": {"created": 0, "completed": 0},
 "state": {"status": "completed", "input": {}, "content": [{"type": "text", "text": "..."}], "metadata": {}}}
```

A failed call has `state: {"status": "error", "input": {}, "error": {"type": "...", "message": "..."}}`.

## Behaviour Decant depends on

- Only messages after the newest completed compaction are replayed to the model.
- Message ids ascend in session order. Session ids descend over time.
- The project id is cached in `<git-common-dir>/opencode`, otherwise it is the first sorted root commit of `HEAD`. Outside git it is `global`.
- `opencode session import` needs `--standalone` to stay away from the shared background service. Verification relies on that plus a private `HOME` and `XDG_*`.

## Adapting to a new version

1. Run `prepare` and read `checks` and `advisories`.
2. `tool_map_drift` lists mapped names the version no longer offers. Update `TOOL_MAP` and `ARG_MAP` in `scripts/decant.py`.
3. For format errors, export a native session containing a tool call (`opencode session export <id>`) and compare it with this page.
4. Update `TESTED_OPENCODE`, this page and `decant-selftest.py`, then run the self-test with `--opencode`.
