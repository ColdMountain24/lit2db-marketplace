#!/bin/sh
# Install the `lit2db` command (D-211). One line, pasted once into Terminal:
#
#   curl -fsSL https://raw.githubusercontent.com/ColdMountain24/lit2db-marketplace/main/install.sh | sh
#
# From a checkout, for development:   sh install.sh --from plugins/lit2db
#
# What it does, in order, and nothing else:
#   1. puts lit2db in ~/.lit2db/app (a download of this repository, or a link to --from)
#   2. puts a `lit2db` command in ~/.local/bin, and adds that folder to your PATH if it is not there
#   3. installs uv if it is missing: a small tool that gives lit2db its own Python, so yours is
#      never touched (https://docs.astral.sh/uv/)
#   4. checks for Claude Code, which reads the papers, and says how to get it if it is missing
set -eu

REPO="ColdMountain24/lit2db-marketplace"
BRANCH="main"
DEST="$HOME/.lit2db/app"
BIN="$HOME/.local/bin"
FROM=""
if [ "${1:-}" = "--from" ] && [ -n "${2:-}" ]; then
  FROM=$(CDPATH= cd -- "$2" && pwd)
fi

say() { printf '%s\n' "$*"; }
say ""
say "Installing lit2db."

mkdir -p "$HOME/.lit2db" "$BIN"
if [ -n "$FROM" ]; then
  rm -rf "$DEST"
  ln -s "$FROM" "$DEST"
  say "  using the copy at $FROM"
else
  TMP=$(mktemp -d)
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar -xz -C "$TMP"
  rm -rf "$DEST"
  mv "$TMP"/*/plugins/lit2db "$DEST"
  rm -rf "$TMP"
  say "  downloaded to $DEST"
fi
ln -sf "$DEST/bin/lit2db" "$BIN/lit2db"

case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    PROFILE="$HOME/.zshrc"
    [ "$(basename "${SHELL:-zsh}")" = "bash" ] && PROFILE="$HOME/.bash_profile"
    printf '\nexport PATH="$HOME/.local/bin:$PATH"   # added by the lit2db installer\n' >> "$PROFILE"
    say "  added ~/.local/bin to your PATH (in $PROFILE)"
    ;;
esac

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  say "  installing uv, so lit2db has its own Python"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi
# The first run downloads lit2db's Python and three packages; do it now, not at first use.
"$BIN/lit2db" version >/dev/null

say ""
if command -v claude >/dev/null 2>&1; then
  say "Done. Open a new Terminal window and type:  lit2db"
else
  say "Done, with one thing left. lit2db reads papers with Claude Code, which is not installed."
  say "Get it from https://claude.com/claude-code, then type  claude  once to sign in."
  say "After that, open a new Terminal window and type:  lit2db"
fi
say ""
