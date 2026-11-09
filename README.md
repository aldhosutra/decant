# /decant

[![version](https://img.shields.io/github/v/tag/aldhosutra/decant?label=version&color=1f6feb)](https://github.com/aldhosutra/decant/releases)
[![license](https://img.shields.io/github/license/aldhosutra/decant?color=2da44e)](LICENSE)
![agents](https://img.shields.io/badge/agents-Claude%20Code%20%7C%20opencode-1f6feb)
![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)

Pour a Claude Code session into OpenCode and keep working. The conversation comes across as a real,
resumable OpenCode session, and the sediment stays behind.

Hit a usage limit halfway through something? Run `/decant <session-id>`, then `opencode -s <id>`.

## Why "decant"

To decant wine is to pour it slowly from its bottle into a new vessel, stopping before the sediment
comes through. That is the whole design:

- **It is the same wine.** Decant moves the conversation itself, as a native session you can resume.
  A handoff note is a description of the wine.
- **The sediment stays behind on purpose.** Private thinking, images and abandoned branches don't
  belong in the new vessel, and every one left out is counted, so nothing vanishes silently.
- **You pour with care.** The result is tasted first in a throwaway OpenCode, and only the exact pour
  that passed can go into your real one.
- **Wine needs room to breathe.** A long session would fill a model's whole context, so Decant gives it
  space: the earlier conversation is condensed and the latest turns stay whole.

The metaphor stops in one place: your original bottle stays full. The Claude Code session is only read.

## Installation

With the Skills CLI:

```bash
npx skills add aldhosutra/decant --global
```

Leave off `--global` to install into the current project only. The skill answers to `/decant`.

Claude Code can install it as a plugin instead, which `/plugin update` then keeps current:

```text
/plugin marketplace add aldhosutra/decant
/plugin install decant@aldhosutra
```

This is the shared `aldhosutra` marketplace, which also carries
[supervise](https://github.com/aldhosutra/supervise), [loopa](https://github.com/aldhosutra/loopa)
and [experso](https://github.com/aldhosutra/experso).

Or in one line, without either:

```bash
curl -fsSL https://raw.githubusercontent.com/aldhosutra/decant/main/install.sh | bash
```

From a clone, run `./install.sh`, or `./install.sh --project` to install into the current repo.

| Agent | Invoke it | Where it finds the skill |
|---|---|---|
| Claude Code | `/decant` | `~/.claude/skills`, `.claude/skills` |
| OpenCode | `/decant` | `~/.agents/skills`, `~/.claude/skills`, project equivalents |
| Codex | `$decant` | `~/.agents/skills`, `.agents/skills` |

You need Python 3.9+ and OpenCode 2.x. `tmux` is optional and enables the interface check.

## How it works

1. **Find.** Give it an id, a prefix or `--latest`. It searches every `~/.claude*` profile.
2. **Convert.** It follows the active branch of the transcript into OpenCode's native format.
3. **Prove.** It imports the result into a throwaway OpenCode with a private home directory, resumes
   against a fake local model, and checks the round trip, tool pairing, replay window and interface.
4. **Import.** With your agreement, it imports through OpenCode's own command. Only the exact file
   that passed can be imported, and your Claude Code session is never changed.

## What comes across

| | |
|---|---|
| Messages and visible replies | All of the active branch |
| Tool calls and output | Yes, mapped to OpenCode's tool names |
| Context for the model | Summary of the conversation plus the most recent turns in full |
| Images | Replaced by a text placeholder |
| Private thinking | Readable blocks stay in older history and are never replayed |
| Subagent conversations | Left behind; the calls that started them remain |
| Abandoned branches | Left behind |

The fake model proves that a session imports, replays and continues. A real provider can still have
rules of its own, so the first real turn is the final test. Tested on OpenCode 2.2.6; other 2.x
versions are checked at run time and flagged. See
[the format notes](plugins/decant/skills/decant/references/opencode-2x.md) for adapting to a new one.

## Development

```bash
python3 plugins/decant/skills/decant/scripts/decant-selftest.py             # offline
python3 plugins/decant/skills/decant/scripts/decant-selftest.py --opencode  # also in a throwaway OpenCode
```

## License

MIT
