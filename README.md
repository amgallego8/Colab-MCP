# Colab-MCP

An MCP server that lets an AI agent (Claude, or any MCP client) **use Google Colab end to end**: create and stop GPU/TPU runtimes, run code, work in notebooks cell by cell, launch long background jobs, and move files and folders in and out of the VM.

It is a thin, safe layer over Google's official **Colab CLI** ([`google-colab-cli`](https://github.com/googlecolab/google-colab-cli)). It does not reimplement Colab and does not depend on a browser tab.

> 🇪🇸 **Resumen en español al final de este archivo.**

## Status

- **Tested against a simulated CLI** (92 checks, `tests/`): a real MCP client talks to the server over stdio and the code that normally runs on the VM is executed for real in a sandbox folder.
- **Not yet validated against live Colab.** Sign-in and a first real run are the next step; behaviours the CLI does not document (listed under *Known unknowns*) are flagged below.
- Linux and macOS only (the Colab CLI does not support Windows).

## Tools (36)

| Area | Tools |
|---|---|
| **Runtimes** | `colab_new`, `colab_ensure_session`, `colab_sessions`, `colab_status`, `colab_runtime_info`, `colab_restart_kernel`, `colab_url`, `colab_usage`, `colab_stop`, `colab_install` |
| **Run code** | `colab_exec` (code or file; kernel state persists between calls), `colab_run` (one-shot: fresh VM → script → release) |
| **Notebooks** (local `.ipynb`) | `colab_notebook_create`, `_list`, `_read`, `_add_cell`, `_edit_cell`, `_delete_cell`, `_move_cell`, `_clear_outputs`, `_run_cell`, `_run_all`, `_to_script`, `_delete` |
| **Long jobs** | `colab_job_start`, `_status`, `_logs`, `_cancel`, `_list` |
| **Files** | `colab_upload`, `colab_download`, `colab_upload_dir`, `colab_download_dir`, `colab_ls`, `colab_rm`, `colab_log` |

Typical flows:

- **Interactive:** `colab_ensure_session(gpu="T4")` → `colab_notebook_create` → add cells → `colab_notebook_run_cell` one by one (variables persist) → read outputs → `colab_stop`.
- **Batch:** `colab_upload_dir` → `colab_job_start("python -u train.py")` → poll `colab_job_logs` → `colab_download_dir` → `colab_stop`.

Deliberately **not** included: sign-in, `colab auth`, `colab drivemount`, `repl`, `console`, `ssh`, `edit`. They are interactive and cannot work through a tool call.

## Security model

- **Local paths are confined** to `COLAB_MCP_WORKDIR`. `../`, absolute paths and symlinks that escape are rejected before anything runs.
- **Secrets never reach the model.** `COLAB_MCP_ENV_FILE` points to a local `NAME=value` file. The model only passes names (`secrets=["OPENROUTER_API_KEY"]`); values go to the kernel with `--env` and are scrubbed from every result. Known limit: while the command runs, the value is visible to other local users in the process list, because the CLI takes it as an argument.
- **No shell.** The CLI is run with an argument list. Session names, GPU types, package specifiers and job ids are validated. Everything user-controlled that is embedded in code for the VM goes through `repr()`.
- **Folder transfers are guarded.** Uploads skip `.git`, caches, symlinks and likely secrets (`.env`, `*.pem`, `*.key`…); downloads reject archives with paths that escape the target (zip-slip) or exceed a size limit.
- **No hard deletes on your computer.** `colab_notebook_delete` moves the file to `.colab-mcp-trash/`. `colab_rm` only touches the VM and refuses root-like paths.
- Output is clipped to `COLAB_MCP_MAX_OUTPUT` characters.
- Code you run executes on Google's machines **under your account and spends your compute units**. Stop sessions you no longer need.

## Install

```bash
# 1. uv and the official CLI
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install google-colab-cli

# 2. Sign in ONCE (open the link, paste the code)
colab sessions

# 3. This server
git clone https://github.com/amgallego8/Colab-MCP ~/Colab-MCP
```

Optional secrets file (outside any repository, readable only by you):

```bash
mkdir -p ~/.config/colab-mcp
printf 'OPENROUTER_API_KEY=your-key-here\n' > ~/.config/colab-mcp/secrets.env
chmod 600 ~/.config/colab-mcp/secrets.env
```

Register in **Claude Code**:

```bash
claude mcp add colab-cli \
  -e COLAB_MCP_WORKDIR="$HOME/colab-work" \
  -e COLAB_MCP_ENV_FILE="$HOME/.config/colab-mcp/secrets.env" \
  -- uv run --directory ~/Colab-MCP colab-cli-mcp
```

Register in **Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "colab-cli": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/YOU/Colab-MCP", "colab-cli-mcp"],
      "env": {
        "COLAB_MCP_WORKDIR": "/Users/YOU/colab-work",
        "COLAB_MCP_ENV_FILE": "/Users/YOU/.config/colab-mcp/secrets.env"
      }
    }
  }
}
```

Every person who uses it repeats these steps with **their own Google account**: the sign-in token is personal and must not be shared.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `COLAB_MCP_WORKDIR` | current directory | The only local folder the server touches |
| `COLAB_MCP_ENV_FILE` | — | `NAME=value` file with secrets |
| `COLAB_MCP_AUTH` | CLI default | `oauth2` or `adc` (the CLI's `--auth`) |
| `COLAB_MCP_MAX_OUTPUT` | `20000` | Max characters returned per call |
| `COLAB_MCP_MAX_UPLOAD_MB` | `100` | Per-file/zip upload cap (the CLI sends a file in one request) |
| `COLAB_MCP_MAX_DOWNLOAD_MB` | `1024` | Cap for unpacked directory downloads |
| `COLAB_BIN` | `colab` | Path to the CLI executable |

## How notebooks work

Notebooks are ordinary `.ipynb` files inside the working directory, edited locally with `nbformat`, so editing needs no runtime. `colab_notebook_run_cell` sends one cell to the runtime through `colab exec`; the kernel keeps its variables between calls, and the cell's output (text, errors, a displayed plot) is written back into the notebook. Cells are addressed by **id** (stable) or index. Plots are saved under `.colab-mcp/images/`.

`colab_exec` with a `.ipynb` file runs the whole notebook and the CLI saves `<name>_output.ipynb` beside it.

## Known unknowns (to confirm against live Colab)

These come from reading the CLI's source and docs, not from running it:

- Kernel variables persisting across `exec` calls (the source reuses the same kernel id, so it should).
- Cell success/failure detection relies on `get_ipython().run_cell` being available in the Colab kernel.
- Whether `--output-image` saves every image form (it handles PNG/JPEG display data).
- The real upload size limit (the CLI sends a file in a single request; the 100 MB cap is a conservative guess).
- Whether Colab *Secrets* (`userdata`) are readable in the kernel when running through the CLI — undocumented. Use `secrets=[...]` instead.
- Hosted notebooks in Google Drive (create/delete from Drive) are **not** covered: the CLI has no such command.

## Develop

```bash
pip install "mcp<2" nbformat
python tests/test_server.py
```

The SDK pin `mcp<2` is intentional: 2.x renamed `FastMCP` to `MCPServer`.

## License

Not chosen yet.

---

## Resumen en español

Servidor MCP para que un agente use **Google Colab de punta a punta**: crear y detener runtimes con GPU/TPU, ejecutar código, trabajar en cuadernos celda por celda, lanzar trabajos largos en segundo plano y mover archivos y carpetas hacia y desde la VM. Envuelve el **Colab CLI oficial** de Google.

- **Estado:** probado con un CLI simulado (92 comprobaciones). **Falta validar con Colab real** (ver «Known unknowns»).
- **Seguridad:** las rutas locales quedan confinadas a `COLAB_MCP_WORKDIR`; las claves viven en un archivo local y nunca se devuelven al modelo; no hay shell; los borrados de cuadernos van a una papelera recuperable.
- **Instalación:** cada persona repite los pasos de «Install» con su propia cuenta de Google. El token de sesión es personal.
- **Costo:** lo que ejecutes consume tus unidades de cómputo de Colab. Detén las sesiones que no uses.
