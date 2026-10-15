# Colab-MCP

An MCP server that lets an AI agent (Claude, or any MCP client) **use Google Colab end to end**: create and stop GPU/TPU runtimes, run code, work in notebooks cell by cell, launch long background jobs, and move files and folders in and out of the VM.

It is a thin, safe layer over Google's official **Colab CLI** ([`google-colab-cli`](https://github.com/googlecolab/google-colab-cli)). It does not reimplement Colab and does not depend on a browser tab.

> 🇪🇸 **Resumen en español al final de este archivo.**

## Status

- **Tested against a simulated CLI** (92 checks, plus 53 for Drive against a simulated Drive API; `tests/`): a real MCP client talks to the server over stdio and the code that normally runs on the VM is executed for real in a sandbox folder.
- **Not yet validated against live Colab.** Sign-in and a first real run are the next step; behaviours the CLI does not document (listed under *Known unknowns*) are flagged below.
- Linux and macOS only (the Colab CLI does not support Windows).

## Tools (38 + 3 optional Drive-notebook tools)

| Area | Tools |
|---|---|
| **Runtimes** | `colab_new`, `colab_ensure_session`, `colab_sessions`, `colab_status`, `colab_runtime_info`, `colab_restart_kernel`, `colab_url`, `colab_usage`, `colab_stop`, `colab_install` |
| **Run code** | `colab_exec` (code or file; kernel state persists between calls), `colab_run` (one-shot: fresh VM → script → release) |
| **Notebooks** (local `.ipynb`) | `colab_notebook_create`, `_list`, `_read`, `_add_cell`, `_edit_cell`, `_delete_cell`, `_move_cell`, `_clear_outputs`, `_run_cell`, `_run_all`, `_to_script`, `_delete` |
| **Long jobs** | `colab_job_start`, `_status`, `_logs`, `_cancel`, `_list` |
| **Files** | `colab_upload`, `colab_download`, `colab_upload_dir`, `colab_download_dir`, `colab_ls`, `colab_rm`, `colab_log` |
| **Drive on the VM** | `colab_drive_status`, `colab_drive_project_dir` (see *Big projects and Drive*) |

Typical flows:

- **Interactive:** `colab_ensure_session(gpu="T4")` → `colab_notebook_create` → add cells → `colab_notebook_run_cell` one by one (variables persist) → read outputs → `colab_stop`.
- **Batch:** `colab_upload_dir` → `colab_job_start("python -u train.py")` → poll `colab_job_logs` → `colab_download_dir` → `colab_stop`.

Google Drive create/delete is a separate, opt-in feature (next section). Deliberately **not** included anywhere: sign-in through a tool, `colab auth`, `colab drivemount`, `repl`, `console`, `ssh`, `edit`. They are interactive and cannot work through a tool call.

## Security model

- **Local paths are confined** to `COLAB_MCP_WORKDIR`. `../`, absolute paths and symlinks that escape are rejected before anything runs.
- **Secrets never reach the model.** `COLAB_MCP_ENV_FILE` points to a local `NAME=value` file. The model only passes names (`secrets=["OPENROUTER_API_KEY"]`); values go to the kernel with `--env` and are scrubbed from every result. Known limit: while the command runs, the value is visible to other local users in the process list, because the CLI takes it as an argument.
- **No shell.** The CLI is run with an argument list. Session names, GPU types, package specifiers and job ids are validated. Everything user-controlled that is embedded in code for the VM goes through `repr()`.
- **Folder transfers are guarded.** Uploads skip `.git`, caches, symlinks and likely secrets (`.env`, `*.pem`, `*.key`…); downloads reject archives with paths that escape the target (zip-slip) or exceed a size limit.
- **No hard deletes on your computer.** `colab_notebook_delete` moves the file to `.colab-mcp-trash/`. `colab_rm` only touches the VM and refuses root-like paths.
- Output is clipped to `COLAB_MCP_MAX_OUTPUT` characters, and terminal colour codes (tracebacks) are stripped.
- Code you run executes on Google's machines **under your account and spends your compute units**. Stop sessions you no longer need.

## Big projects and Drive on the VM

For big datasets, checkpoints or results that must survive the session, mount Google Drive **on the VM**. This is separate from the optional "Drive notebooks" feature below and uses the Colab CLI's own sign-in.

1. **You mount it, in a terminal** (it needs your approval in a browser, so the agent cannot do it): `colab drivemount -s SESSION`. `colab_drive_status` tells the agent whether it is mounted and, if not, shows you that exact command.
2. The agent calls `colab_drive_project_dir("my-project")`, which creates `/content/drive/MyDrive/Colab-MCP/my-project` and returns the path. Datasets, checkpoints and outputs go there.
3. Stop the session when done. Files in that Drive folder remain; everything else on the VM is lost.

Guardrail: the file tools (`colab_upload*`, `colab_download*`, `colab_ls`, `colab_rm`) **refuse any Drive path outside `/content/drive/MyDrive/Colab-MCP/`** (folder name configurable with `COLAB_MCP_DRIVE_PROJECTS`), including `..`, relative and case tricks. `colab_drive_status` never lists your files.

**Residual risk, stated plainly:** once Drive is mounted, *any code that runs on the VM* (`colab_exec`, `colab_run`, jobs, notebook cells) can read and change **all** of your Drive; the path guard only covers this server's file tools, and it cannot see symlinks created on the VM. Mount only when you need it, review the code you run, and stop the session afterwards.

## Google Drive: create and delete Colab notebooks (opt-in)

Off by default. When `COLAB_MCP_DRIVE=1` three more tools appear, and **only** these three:

| Tool | What it does |
|---|---|
| `drive_notebook_create` | Creates a notebook in a Drive folder named `Colab-MCP` (empty starter, or a local `.ipynb` with its outputs stripped) |
| `drive_notebook_list` | Lists the notebooks *this server* created (needed to find an id) |
| `drive_notebook_delete` | Moves one of them to the Drive **trash** (never permanent) |

A skill that keeps the agent to exactly this lives in [`skills/colab-drive-notebooks/SKILL.md`](skills/colab-drive-notebooks/SKILL.md): copy that folder to `~/.claude/skills/` (Claude Code) or add it as a skill in your client.

### Who is responsible for what

Nobody shares credentials. **Each user creates their own Google Cloud project and OAuth client, signs in themselves in a terminal, and owns the token on their own disk.** This project ships no client ID, no secret and no hosted service. The model never sees a sign-in link, code or token: login is a terminal command, not a tool.

### Quick start (every user)

```bash
git clone https://github.com/amgallego8/Colab-MCP ~/Colab-MCP && cd ~/Colab-MCP
uv sync                              # installs everything, Drive packages included
uv run colab-cli-mcp-drive login     # asks how you want to sign in (below)
```

`login` shows a menu:

1. **shared** — sign in with your browser, nothing to set up. It uses the OAuth client shipped in `colab_cli_mcp/shared_client.json`; the owner of that Google project (the maintainer) answers to Google for the app. Available only in builds that ship one (this repository does not yet).
2. **own** — you create your own Google Cloud project and OAuth client; `login` walks you through it and, when you give it the path of the JSON you downloaded, stores it for you (`0600`). You answer to Google for your project.

The shared client is never used without an explicit choice. Non-interactive use needs `--own` or `--shared`. Sign-in, the `drive.file` scope, the token file and every guardrail are identical in both cases.

Then add `-e COLAB_MCP_DRIVE=1` to the MCP registration (Claude Desktop: `"COLAB_MCP_DRIVE": "1"` in `env`), and leave **per-call approval on** for `drive_notebook_delete` in your MCP client — that prompt is the real human confirmation; the server cannot force it. `uv run colab-cli-mcp-drive status` shows what is configured.

To leave: `colab-cli-mcp-drive logout` (deletes the token and revokes it at Google), and remove the app at <https://myaccount.google.com/permissions> if revocation could not be confirmed.

### Creating your own client by hand (option 2 in detail)

1. In the [Google Cloud console](https://console.cloud.google.com/) create a project and enable the **Google Drive API** ([guide](https://developers.google.com/workspace/guides/enable-apis)).
2. Configure the consent screen / Google Auth Platform ([guide](https://developers.google.com/workspace/guides/configure-oauth-consent)): under **Audience** pick *External* and add yourself under *Test users*; under **Data Access → Add or Remove Scopes** add only `.../auth/drive.file`.
3. **Google Auth platform → Clients → Create Client → Desktop app** ([Google's steps](https://developers.google.com/workspace/guides/create-credentials)). Download the JSON and give its path to `login`.

### For the maintainer: publishing a shared client

Create the client as above (ideally in a dedicated Google account and project), then place the JSON at `colab_cli_mcp/shared_client.json` and commit it on purpose (`git add -f`; the file is git-ignored by default). Desktop-app client secrets are not confidential by design, but the project, its consent screen (support email, privacy policy link) and any Google verification requirements become yours. See [TERMS.md](TERMS.md).

### Threat model (what this does and does not protect)

| Layer | What it gives you |
|---|---|
| Scope `drive.file` only | The token can only reach files this OAuth client created. It cannot read or delete the rest of your Drive, even if everything below failed. Google classifies this scope as non-sensitive. |
| Your own OAuth client | Another user's or a vendor's client cannot touch your files, and you can revoke the whole client from your own project. |
| Token handling | `0600` file, refused if group/world-readable, refused if it has any scope other than `drive.file`. Never returned by any tool. Separate from the Colab CLI's token (which has broader scopes). |
| Tool surface | Create / list / trash. No permanent delete, no sharing, no permissions, no update, no content reads, no free-form queries or ids. A test asserts that no `DELETE` request and no permissions/export/copy endpoint is ever used. |
| Delete guards | The file must carry this server's marker, be a Colab notebook, be owned by you, sit directly in the app folder, not already be trashed, and the caller must retype its exact name. |
| Limits and trace | At most 20 creations and 5 deletions per hour (`COLAB_MCP_DRIVE_MAX_CREATES_PER_HOUR` / `_MAX_DELETES_PER_HOUR`); append-only audit log (`audit.log`, `0600`) that never stores content. |
| Content hygiene | Uploaded notebooks lose their outputs, and an upload is refused if it contains a value from your secrets file. Names read back from Drive have control and bidi characters stripped and are labelled as data. |

**Residual risks, stated plainly.** "Completely secure" is not a claim anyone can make; this is what remains:

- A prompt-injected or mistaken agent can still create junk notebooks (rate-limited) or trash a notebook *this server created* if you approve the call. They stay recoverable in the Drive trash.
- The confirmation-by-name check stops accidents, not a model that is determined to repeat the name; the human approval prompt in your client is the real control.
- Anyone who can read your user account's files can use your token until you log out. Disk encryption and a locked account are on you.
- Notebooks you create can contain code that will run later in Colab with your account; review what you open.
- In *Testing* mode Google expires refresh tokens after 7 days for external apps, so you may need to log in again weekly.
- Not yet run against live Google Drive (see *Known unknowns*).

Env: `COLAB_MCP_DRIVE` (enable), `COLAB_MCP_DRIVE_DIR` (default `~/.config/colab-mcp/drive`), `COLAB_MCP_DRIVE_CLIENT`, `COLAB_MCP_DRIVE_TOKEN`, `COLAB_MCP_DRIVE_AUDIT`, `COLAB_MCP_DRIVE_FOLDER` (default `Colab-MCP`).

## Install

**Fastest:** `bash install.sh` (macOS/Linux). It asks before installing anything, signs you in to Colab, and registers the server with Claude Code (or prints the Claude Desktop config). The manual steps are below.

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
| `COLAB_MCP_DRIVE_PROJECTS` | `Colab-MCP` | The only Drive folder (under MyDrive) the file tools may use when Drive is mounted on the VM |

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
- Google Drive (opt-in, see below): the Colab MIME type `application/vnd.google.colaboratory` was **not** found in Google's Drive documentation. The server checks what Drive stored after each creation and undoes it if it differs, but this has not been run against real Drive yet.
- Whether a Google Cloud app that stays unverified can be used by its owner without the 7-day refresh-token expiry of "Testing" mode (Google's docs say refresh tokens of external apps in Testing expire after 7 days; the rules for unverified apps "In production" were not confirmed).

## Develop

```bash
pip install "mcp<2" nbformat google-auth google-auth-oauthlib requests
python tests/test_server.py   # the Colab tools, with a simulated CLI
python tests/test_drive.py    # the Drive tools, with a simulated Drive API
```

The SDK pin `mcp<2` is intentional: 2.x renamed `FastMCP` to `MCPServer`.

## Terms of use and privacy

See [TERMS.md](TERMS.md): no server and no data collection by the maintainer, what is stored locally, user responsibilities, who answers to Google under each sign-in option, and the no-warranty clause.

## License

Apache License 2.0 — see [LICENSE](LICENSE). Copyright 2026 the Colab-MCP contributors (`amgallego8`). The official Colab CLI this project wraps is a separate work, also Apache-2.0, and is not included here.

---

## Resumen en español

Servidor MCP para que un agente use **Google Colab de punta a punta**: crear y detener runtimes con GPU/TPU, ejecutar código, trabajar en cuadernos celda por celda, lanzar trabajos largos en segundo plano y mover archivos y carpetas hacia y desde la VM. Envuelve el **Colab CLI oficial** de Google.

- **Estado:** probado con un CLI simulado (92 comprobaciones). **Falta validar con Colab real** (ver «Known unknowns»).
- **Seguridad:** las rutas locales quedan confinadas a `COLAB_MCP_WORKDIR`; las claves viven en un archivo local y nunca se devuelven al modelo; no hay shell; los borrados de cuadernos van a una papelera recuperable.
- **Instalación:** cada persona repite los pasos de «Install» con su propia cuenta de Google. El token de sesión es personal.
- **Google Drive (opcional):** crear y borrar cuadernos de Colab en tu Drive, solo eso. Cada usuario crea su propio proyecto y cliente OAuth, inicia sesión él mismo en una terminal y es responsable de su token; el permiso es `drive.file` (solo archivos creados por esta herramienta) y el borrado va a la papelera. Hay una skill (`skills/colab-drive-notebooks`) que limita al agente a eso. Ver «Google Drive» y el modelo de amenazas, con sus riesgos residuales. Aún sin probar contra Drive real.
- **Costo:** lo que ejecutes consume tus unidades de cómputo de Colab. Detén las sesiones que no uses.
