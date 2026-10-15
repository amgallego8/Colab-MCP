#!/usr/bin/env bash
# Colab-MCP installer (macOS / Linux). Asks before it installs or changes anything.
# Usage:  bash install.sh        (from a clone)   or   bash <(curl -fsSL <raw url>/install.sh)
set -euo pipefail

REPO_URL="https://github.com/amgallego8/Colab-MCP"
DIR="${COLAB_MCP_HOME:-$HOME/Colab-MCP}"
WORK="${COLAB_MCP_WORKDIR:-$HOME/colab-work}"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ask()  { local a; read -r -p "$1 [Y/n] " a < /dev/tty || a=n; [[ -z "$a" || "$a" =~ ^[Yy] ]]; }
have() { command -v "$1" >/dev/null 2>&1; }

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) echo "Colab CLI supports macOS and Linux only (on Windows use WSL)."; exit 1 ;;
esac
have git  || { echo "git is required. Install it and run this again."; exit 1; }
have curl || { echo "curl is required."; exit 1; }

say "1/6  uv (Python tool manager)"
if have uv; then echo "already installed"; else
  ask "Install uv from https://astral.sh/uv ?" || { echo "uv is required."; exit 1; }
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

say "2/6  Google's official Colab CLI"
if have colab; then echo "already installed"; else
  ask "Install it with: uv tool install google-colab-cli ?" || { echo "The Colab CLI is required."; exit 1; }
  uv tool install google-colab-cli
  export PATH="$HOME/.local/bin:$PATH"
fi

say "3/6  This project"
if [ -d "$DIR/.git" ]; then
  echo "found $DIR - updating"; git -C "$DIR" pull --ff-only
else
  ask "Download $REPO_URL into $DIR ?" || exit 1
  git clone "$REPO_URL" "$DIR"
fi
(cd "$DIR" && uv sync)
mkdir -p "$WORK"; echo "working folder (the only local folder the tool touches): $WORK"

say "4/6  Sign in to Google (for Colab)"
echo "This is the Colab CLI's own sign-in: it prints a link; open it, accept, and paste the code it shows."
if colab sessions >/dev/null 2>&1; then echo "already signed in"; else
  ask "Sign in now?" && colab sessions || echo "Skipped. Run 'colab sessions' later to sign in."
fi

say "5/6  Google Drive (optional)"
echo "Lets the AI create/open/save Colab notebooks in a Drive folder named Colab-MCP, with one subfolder per project."
echo "Permission requested: drive.file only - files this tool creates; the rest of your Drive stays invisible to it."
echo "Sign-in uses a Google \"Desktop app\" client of your own (one-time, about 10 minutes; the setup walks you through it). After that a link opens in your browser, you click Allow, and it happens by itself whenever needed."
DRIVE=0
if [ -f "$HOME/.config/colab-mcp/drive/token.json" ]; then echo "already signed in to Drive"; DRIVE=1
elif ask "Set up Drive now?"; then
  if (cd "$DIR" && uv run colab-cli-mcp-drive login < /dev/tty); then DRIVE=1; else echo "Drive not set up. You can retry later: cd $DIR && uv run colab-cli-mcp-drive login"; fi
else echo "Skipped. Later: cd $DIR && uv run colab-cli-mcp-drive login"; fi
DRIVE_ENV=(); [ "$DRIVE" = 1 ] && DRIVE_ENV=(-e COLAB_MCP_DRIVE=1)

say "6/6  Connect to your AI client"
CMD=(uv run --directory "$DIR" colab-cli-mcp)
if have claude; then
  if claude mcp get colab-cli >/dev/null 2>&1; then
    if [ "$DRIVE" = 1 ] && ask "Claude Code: already registered. Re-register it so the Drive tools are enabled?"; then
      claude mcp remove -s user colab-cli >/dev/null 2>&1 || true
      claude mcp add -s user colab-cli -e COLAB_MCP_WORKDIR="$WORK" ${DRIVE_ENV[@]+"${DRIVE_ENV[@]}"} -- "${CMD[@]}"
    else echo "Claude Code: already registered"; fi
  elif ask "Register with Claude Code (available in all your projects)?"; then
    claude mcp add -s user colab-cli -e COLAB_MCP_WORKDIR="$WORK" ${DRIVE_ENV[@]+"${DRIVE_ENV[@]}"} -- "${CMD[@]}"
  fi
fi
if [ "$(uname -s)" = Darwin ]; then DESKTOP_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
else DESKTOP_CFG="$HOME/.config/Claude/claude_desktop_config.json"; fi
if [ -d "$(dirname "$DESKTOP_CFG")" ] && have python3; then
  if ask "Register with the Claude desktop app too? (edits $DESKTOP_CFG, keeps a .bak backup)"; then
    python3 "$DIR/scripts/register_desktop.py" "$DESKTOP_CFG" "$(command -v uv)" "$DIR" "$WORK" $([ "$DRIVE" = 1 ] && echo drive)
  fi
else
  echo "Claude Desktop: add this under \"mcpServers\" in claude_desktop_config.json, then restart the app:"
  cat <<JSON
    "colab-cli": {
      "command": "$(command -v uv)",
      "args": ["run", "--directory", "$DIR", "colab-cli-mcp"],
      "env": { "COLAB_MCP_WORKDIR": "$WORK"$([ "$DRIVE" = 1 ] && echo ', "COLAB_MCP_DRIVE": "1"') }
    }
JSON
fi

say "Done."
echo "Restart your AI client so it loads the tools, then ask it to list your Colab sessions."
[ "$DRIVE" = 1 ] || echo "Optional - Google Drive: cd $DIR && uv run colab-cli-mcp-drive login, then re-run this installer to enable the tools."
