"""Add (or update) the colab-cli server in a Claude Desktop config file, keeping a backup.

Usage: register_desktop.py CONFIG_PATH UV_PATH PROJECT_DIR WORKDIR [drive]
("drive" also sets COLAB_MCP_DRIVE=1 so the Drive tools are enabled.)
Touches only mcpServers["colab-cli"]; every other setting is preserved.
"""
import json
import shutil
import sys
from pathlib import Path

cfg, uv, project, work = sys.argv[1:5]
with_drive = len(sys.argv) > 5 and sys.argv[5] == "drive"
path = Path(cfg)
data = {}
if path.exists():
    shutil.copy(path, str(path) + ".bak")
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError:
        sys.exit(f"{path} is not valid JSON; fix it first (nothing was changed).")
    if not isinstance(data, dict):
        sys.exit(f"{path} has an unexpected format (nothing was changed).")
servers = data.setdefault("mcpServers", {})
servers["colab-cli"] = {
    "command": uv,
    "args": ["run", "--directory", project, "colab-cli-mcp"],
    "env": {"COLAB_MCP_WORKDIR": work, **({"COLAB_MCP_DRIVE": "1"} if with_drive else {})},
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2), encoding="utf-8")
print(f"Registered in {path}" + (f" (backup: {path}.bak)" if path.exists() and Path(str(path) + '.bak').exists() else ""))
