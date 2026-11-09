#!/usr/bin/env bash
# Install the decant skill, for Claude Code and opencode.
#
#   ./install.sh                     from a clone
#   curl -fsSL <raw-url> | bash      from anywhere (clones itself)
#   ./install.sh --project           into ./.claude/skills of the current repo
#
# The skill goes to ~/.claude/skills (or $CLAUDE_CONFIG_DIR/skills). opencode reads
# ~/.claude/skills too, so one install serves both agents; when opencode is present
# its /decant command is added as well.
#
# Claude Code users may prefer the plugin route instead, which handles updates:
#   /plugin marketplace add aldhosutra/decant
#   /plugin install decant@aldhosutra
set -euo pipefail

REPO_URL="https://github.com/aldhosutra/decant.git"
SKILL_SUBPATH="plugins/decant/skills/decant"
COMMAND_SUBPATH="plugins/decant/opencode/command/decant.md"
SCOPE="user"
[ "${1:-}" = "--project" ] && SCOPE="project"

for tool in git python3 opencode; do
  command -v "$tool" >/dev/null 2>&1 || echo "warning: $tool not found — the skill needs it" >&2
done
command -v tmux >/dev/null 2>&1 || echo "note: tmux not found — the interface check will be skipped" >&2

# Work out where the skill is coming from. When piped through bash there is no
# script on disk to locate, so fetch a copy.
ROOT=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [ -d "$HERE/$SKILL_SUBPATH" ] && ROOT="$HERE"
fi
CLONE=""
if [ -z "$ROOT" ]; then
  CLONE="$(mktemp -d)"
  trap 'rm -rf "$CLONE"' EXIT
  echo "fetching $REPO_URL"
  git clone --depth 1 -q "$REPO_URL" "$CLONE/decant"
  ROOT="$CLONE/decant"
fi
SRC="$ROOT/$SKILL_SUBPATH"
[ -d "$SRC" ] || { echo "cannot find the skill at $SRC" >&2; exit 1; }

if [ "$SCOPE" = "project" ]; then
  DEST="$PWD/.claude/skills/decant"
else
  DEST="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/decant"
fi

mkdir -p "$(dirname "$DEST")"
[ -e "$DEST" ] && { echo "replacing the existing install at $DEST"; rm -rf "$DEST"; }
cp -R "$SRC" "$DEST"
find "$DEST/scripts" -type f -name '*.py' -exec chmod +x {} + 2>/dev/null || true

echo
echo "installed to $DEST"

# opencode discovers skills in ~/.claude/skills as well as its own directories, so
# the copy above is all it needs. Its slash commands live elsewhere.
if command -v opencode >/dev/null 2>&1; then
  if [ "$SCOPE" = "project" ]; then
    CMD_DEST="$PWD/.opencode/command/decant.md"
  else
    CMD_DEST="$HOME/.config/opencode/command/decant.md"
  fi
  if [ -f "$ROOT/$COMMAND_SUBPATH" ]; then
    mkdir -p "$(dirname "$CMD_DEST")"
    cp "$ROOT/$COMMAND_SUBPATH" "$CMD_DEST"
    echo "installed the opencode command to $CMD_DEST"
  fi
fi

echo
echo "Start a new session, then:"
echo "  /decant <claude-session-id>"
