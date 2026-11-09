---
description: Pour a Claude Code session into a resumable OpenCode session
agent: build
---

Use the `decant` skill: call `skill({ name: "decant" })` and follow it.

Claude Code session id or prefix (may be empty):

$ARGUMENTS

If nothing was given, list the newest Claude Code sessions for this project with the skill's
`scripts/decant.py locate --latest` and ask which one to bring over.
