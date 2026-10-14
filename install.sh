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

say "1/5  uv (Python tool manager)"
if have uv; then echo "already installed"; else
  ask "Install uv from https://astral.sh/uv ?" || { echo "uv is required."; exit 1; }
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

say "2/5  Google's official Colab CLI"
if have colab; then echo "already installed"; else
  ask "Install it with: uv tool install google-colab-cli ?" || { echo "The Colab CLI is required."; exit 1; }
  uv tool install google-colab-cli
  export PATH="$HOME/.local/bin:$PATH"
fi

say "3/5  This project"
if [ -d "$DIR/.git" ]; then
  echo "found $DIR - updating"; git -C "$DIR" pull --ff-only
else
  ask "Download $REPO_URL into $DIR ?" || exit 1
  git clone "$REPO_URL" "$DIR"
fi
(cd "$DIR" && uv sync)
mkdir -p "$WORK"; echo "working folder (the only local folder the tool touches): $WORK"

say "4/5  Sign in to Google (for Colab)"
echo "This is the Colab CLI's own sign-in: it prints a link; open it, accept, and paste the code it shows."
if colab sessions >/dev/null 2>&1; then echo "already signed in"; else
  ask "Sign in now?" && colab sessions || echo "Skipped. Run 'colab sessions' later to sign in."
fi

say "5/5  Connect to your AI client"
CMD=(uv run --directory "$DIR" colab-cli-mcp)
if have claude && ask "Register with Claude Code now?"; then
  claude mcp add colab-cli -e COLAB_MCP_WORKDIR="$WORK" -- "${CMD[@]}"
else
  echo "Claude Desktop: add this to claude_desktop_config.json under \"mcpServers\", then restart the app:"
  cat <<JSON
    "colab-cli": {
      "command": "$(command -v uv)",
      "args": ["run", "--directory", "$DIR", "colab-cli-mcp"],
      "env": { "COLAB_MCP_WORKDIR": "$WORK" }
    }
JSON
fi

say "Done."
echo "Restart your AI client so it loads the tools, then ask it to list your Colab sessions."
echo "Optional - Google Drive notebooks: cd $DIR && uv run colab-cli-mcp-drive login   (see README)"
