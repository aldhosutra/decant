# decant

**decant** pours a Claude Code session into a resumable OpenCode 2.x session. It is distributed as one agent skill; the logic is one standard-library Python script inside it.

## Repo layout

```
plugins/decant/
  .claude-plugin/plugin.json      plugin manifest
  opencode/command/decant.md      OpenCode slash command
  skills/decant/SKILL.md          the workflow
  skills/decant/references/       troubleshooting, target format
  skills/decant/scripts/          decant.py, decant-selftest.py
.agents/skills/                   link to the skill (.claude/skills links here)
.claude-plugin/marketplace.json   shared "aldhosutra" marketplace (also in supervise, loopa, experso)
install.sh  README.md  LICENSE
```

## Rules

1. Never write to a real OpenCode store outside `decant.py import`, and never without the user's agreement.
2. Verification stays isolated: private `HOME` and `XDG_*`, `--standalone`, an empty workspace, a local fake model, no credentials.
3. Conversion stays deterministic: the same transcript and options give a byte-identical session file.
4. Claude thinking is never replayed, because its signature cannot be carried over.
5. Report loss honestly: every dropped or replaced thing is counted in the manifest.
6. Run `scripts/decant-selftest.py --opencode` after changing the converter.
