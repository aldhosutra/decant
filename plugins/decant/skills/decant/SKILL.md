---
name: decant
description: Pour a Claude Code session into OpenCode so the same conversation can continue there, with its history, tool calls and context intact. Proves the conversion in a throwaway OpenCode before touching the real one. Use whenever the user wants to move, port, convert, migrate, transfer, hand off, resume or continue a Claude Code session or conversation in OpenCode, even if they only give a session id, say "pick this up in opencode", or are switching agents because of limits or cost. Not for exporting a transcript to read, or for moving OpenCode sessions into Claude Code.
---

# Decant

Decant moves a Claude Code conversation into OpenCode 2.x through OpenCode's own `session import`.
Like decanting wine, the conversation is poured across and the sediment stays behind: private
thinking, images and abandoned branches. The user can then run `opencode -s <id>` and keep working.

All commands below are `python3 <this skill>/scripts/decant.py ...`, and each prints JSON.

## Workflow

### 1. Find the session

Accept an id, a prefix, or "my latest session here".

```
decant.py locate <id-or-prefix>
decant.py locate --latest --project <folder>
```

It searches `$CLAUDE_CONFIG_DIR` and every `~/.claude*` profile. If several sessions match, show the
candidates and ask which one. Do not guess: a wrong pick looks fine until the user is deep in it.

### 2. Convert and prove it

```
decant.py prepare <id-or-prefix> [--directory <folder>] [--title "<title>"]
```

This writes the converted session to `~/.cache/decant/<session>/`, then tests it in a private
OpenCode with its own home directory and an empty workspace. A fake local model stands in for the
real one, so the test uses no credentials, costs nothing, and cannot start the project's plugins or
MCP servers. It takes a minute or two.

Use `--directory` when the project moved. The default is the existing folder the session mostly ran in.

### 3. Report before importing

Tell the user, in plain words:

- whether verification passed, and any check that failed;
- what will carry over and what will not, using the numbers in `manifest.stats`: messages, tool calls,
  `images_replaced`, `reasoning_removed_from_replay`, `offpath_records_dropped`, subagent records;
- how much context a resumed turn sends (`verify.replay.approx_tokens`), and any advisories;
- that the real model has not been tested, because the proof uses a fake one.

If it failed, stop and read `references/troubleshooting.md`. Do not work around a failed check: the
gate exists so a bad conversion never lands beside the user's real sessions.

### 4. Import, once the user agrees

Importing adds one session to the user's real OpenCode, so it is their decision. After they agree:

```
decant.py import ~/.cache/decant/<session> --yes
```

It refuses a file that failed verification or changed since. On success it deletes the cached copy,
which holds the whole transcript in plain text. Give the user the `resume` command it prints.

## Setting expectations

- **Readable history:** the active branch, with all messages, replies and tool output.
- **What the model sees:** long sessions get a compaction holding the earlier summary plus every
  message and visible reply verbatim, then the most recent turns in full. Tool output before that
  point is not sent; the summary points to the original transcript so the model can look it up.
- **Claude-only tools** remain as history but cannot be called again.
- **Subagent conversations** stay behind; the calls that started them come across.
- **The original** Claude Code session is only read, never changed.

Lower `--tail-chars` or `--max-summary-chars` for a model with a small context window. To import
the same session a second time as a separate copy, pass any `--salt` text to `prepare`.

## References

- `references/troubleshooting.md`: read when `prepare` or `import` fails.
- `references/opencode-2x.md`: the target format, and how to adapt Decant to a new OpenCode version.
  Read it when advisories report `tool_map_drift` or `untested_version` with failing checks.
- `scripts/decant-selftest.py`: offline checks for the converter. Run after changing it.
