"""Servidor MCP que envuelve el Colab CLI oficial de Google (`google-colab-cli`).

Corre en el equipo del usuario, habla MCP por stdio y ejecuta el comando `colab`
con una lista de argumentos (nunca a través de un shell).

Garantías de diseño:
- Las rutas LOCALES (subir, descargar, ejecutar un archivo) quedan confinadas a
  la carpeta COLAB_MCP_WORKDIR. Una ruta fuera de ella se rechaza.
- Los secretos viven en un archivo local (COLAB_MCP_ENV_FILE). El modelo solo
  conoce los NOMBRES; los valores se pasan al kernel remoto con `--env` y se
  eliminan de cualquier salida devuelta.
- La salida se recorta a COLAB_MCP_MAX_OUTPUT caracteres.
- No hay herramientas para iniciar sesión, montar Drive ni `colab auth`: son
  flujos interactivos. El inicio de sesión se hace una vez en una terminal.
"""

import functools
import os
import re
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("colab-cli")

COLAB_BIN = os.environ.get("COLAB_BIN", "colab")
AUTH = os.environ.get("COLAB_MCP_AUTH")  # "oauth2" | "adc" | None (usa el valor por defecto del CLI)
ENV_FILE = os.environ.get("COLAB_MCP_ENV_FILE")
MAX_OUT = int(os.environ.get("COLAB_MCP_MAX_OUTPUT", "20000"))

SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-\[\],<>=!~+]*$")
GPUS = {"T4", "L4", "G4", "H100", "A100"}
TPUS = {"v5e1", "v6e1"}
EVENT_RE = re.compile(r"^[a-z_]{1,40}$")


def _workdir() -> Path:
    return Path(os.environ.get("COLAB_MCP_WORKDIR") or Path.cwd()).resolve()


class ToolError(Exception):
    """Entrada inválida: se devuelve al modelo como texto, sin ejecutar nada."""


def _session_args(session: str | None) -> list[str]:
    if session is None:
        return []
    if not SESSION_RE.match(session):
        raise ToolError("Nombre de sesión inválido: usa solo letras, números, '-' y '_' (máx. 40).")
    return ["-s", session]


def _local_path(path: str) -> Path:
    """Resuelve `path` dentro de la carpeta de trabajo; rechaza lo que se salga de ella."""
    root = _workdir()
    p = Path(path)
    resolved = (p if p.is_absolute() else root / p).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ToolError(f"Ruta local fuera de la carpeta de trabajo permitida ({root}).") from None
    return resolved


def _load_secrets() -> dict[str, str]:
    """Lee KEY=VALUE del archivo de secretos (si existe). Se relee en cada llamada."""
    if not ENV_FILE:
        return {}
    path = Path(ENV_FILE).expanduser()
    if not path.is_file():
        return {}
    secrets: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if KEY_RE.match(key) and value:
            secrets[key] = value
    return secrets


def _scrub(text: str, secrets: dict[str, str]) -> str:
    for value in secrets.values():
        if len(value) >= 4:
            text = text.replace(value, "***")
    return text


def _clip(text: str) -> str:
    if len(text) <= MAX_OUT:
        return text
    head = MAX_OUT * 2 // 3
    tail = MAX_OUT - head
    return f"{text[:head]}\n... [salida recortada: {len(text) - MAX_OUT} caracteres] ...\n{text[-tail:]}"


def _run(args: list[str], *, stdin: str | None = None, timeout: float = 120.0) -> str:
    secrets = _load_secrets()
    cmd = [COLAB_BIN] + (["--auth", AUTH] if AUTH else []) + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(_workdir()),
            **({"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}),
        )
    except FileNotFoundError:
        return (
            f"ERROR: no se encontró el ejecutable '{COLAB_BIN}'. Instala el CLI "
            "(`uv tool install google-colab-cli`) o define COLAB_BIN con su ruta."
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or b"")
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return _clip(_scrub(f"ERROR: tiempo agotado tras {timeout:.0f} s.\n{partial}", secrets))
    body = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    body = _clip(_scrub(body.strip(), secrets))
    if proc.returncode == 0:
        return body or "OK"
    hint = ""
    low = body.lower()
    if any(k in low for k in ("auth", "credential", "login", "token", "401", "403")):
        hint = (
            "\n\nPosible causa: sesión de Google no iniciada o vencida. Ejecuta `colab sessions` "
            "una vez en una terminal para iniciar sesión."
        )
    return f"ERROR (código {proc.returncode}):\n{body}{hint}"


def _guard(fn):
    """Convierte ToolError en texto para el modelo."""

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except ToolError as exc:
            return f"ERROR: {exc}"

    return wrapper


# --------------------------------------------------------------------------- estado


@mcp.tool()
@_guard
def colab_sessions() -> str:
    """Lista las sesiones (runtimes) activas de Colab. Sirve también para comprobar que el inicio de sesión funciona."""
    return _run(["sessions"], timeout=60)


@mcp.tool()
@_guard
def colab_status(session: str | None = None) -> str:
    """Muestra hardware, forma de la máquina y estado de una sesión (o de la única activa)."""
    return _run(["status", *_session_args(session)], timeout=60)


@mcp.tool()
@_guard
def colab_usage() -> str:
    """Muestra el ritmo de consumo y el saldo de unidades de cómputo de la cuenta."""
    return _run(["usage"], timeout=60)


# --------------------------------------------------------------------------- runtime


@mcp.tool()
@_guard
def colab_new(
    session: str | None = None,
    gpu: str | None = None,
    tpu: str | None = None,
    high_mem: bool = False,
) -> str:
    """Crea un runtime nuevo. Sin gpu ni tpu es CPU. gpu: T4, L4, G4, H100 o A100. tpu: v5e1 o v6e1. high_mem requiere Colab Pro."""
    if gpu and tpu:
        raise ToolError("Elige gpu o tpu, no ambos.")
    if gpu and gpu.upper() not in GPUS:
        raise ToolError(f"gpu inválida. Opciones: {', '.join(sorted(GPUS))}.")
    if tpu and tpu not in TPUS:
        raise ToolError(f"tpu inválida. Opciones: {', '.join(sorted(TPUS))}.")
    args = ["new", *_session_args(session)]
    if gpu:
        args += ["--gpu", gpu.upper()]
    if tpu:
        args += ["--tpu", tpu]
    if high_mem:
        args.append("--high-mem")
    return _run(args, timeout=300)


@mcp.tool()
@_guard
def colab_install(packages: list[str], session: str | None = None) -> str:
    """Instala paquetes de Python en el runtime (usa uv y recurre a pip)."""
    if not packages:
        raise ToolError("Indica al menos un paquete.")
    for pkg in packages:
        if not PKG_RE.match(pkg):
            raise ToolError(f"Especificación de paquete no permitida: {pkg!r}.")
    return _run(["install", *_session_args(session), *packages], timeout=600)


@mcp.tool()
@_guard
def colab_stop(session: str | None = None) -> str:
    """Detiene una sesión y libera la máquina (deja de consumir unidades). Se pierden los archivos que no se hayan descargado."""
    return _run(["stop", *_session_args(session)], timeout=120)


# --------------------------------------------------------------------------- ejecución


@mcp.tool()
@_guard
def colab_exec(
    code: str | None = None,
    file: str | None = None,
    session: str | None = None,
    timeout: float = 120.0,
    secrets: list[str] | None = None,
) -> str:
    """Ejecuta Python en el runtime. Da `code` (texto) o `file` (ruta local .py/.ipynb dentro de la carpeta de trabajo), no ambos.

    `secrets` es una lista de NOMBRES de variables definidas en el archivo de secretos del usuario
    (p. ej. ["OPENROUTER_API_KEY"]); quedan disponibles como variables de entorno en el kernel remoto
    y sus valores nunca se devuelven. `timeout` en segundos (el CLI usa 30 por defecto).
    """
    if (code is None) == (file is None):
        raise ToolError("Indica exactamente uno: `code` o `file`.")
    if not 1 <= timeout <= 7200:
        raise ToolError("timeout debe estar entre 1 y 7200 segundos.")
    args = ["exec", *_session_args(session), "--timeout", str(timeout)]
    if secrets:
        available = _load_secrets()
        for name in secrets:
            if name not in available:
                known = ", ".join(sorted(available)) or "ninguno"
                raise ToolError(f"El secreto {name!r} no existe en el archivo de secretos (disponibles: {known}).")
            args += ["--env", f"{name}={available[name]}"]
    stdin = None
    if file is not None:
        args += ["-f", str(_local_path(file))]
    else:
        stdin = code
    return _run(args, stdin=stdin, timeout=timeout + 60)


@mcp.tool()
@_guard
def colab_run(
    script: str,
    args: list[str] | None = None,
    gpu: str | None = None,
    high_mem: bool = False,
    keep: bool = False,
    timeout: float = 1800.0,
) -> str:
    """Trabajo de una sola vez: crea una máquina nueva, ejecuta un script local (dentro de la carpeta de trabajo), y la libera. Con keep=true la deja viva."""
    if gpu and gpu.upper() not in GPUS:
        raise ToolError(f"gpu inválida. Opciones: {', '.join(sorted(GPUS))}.")
    cmd = ["run"]
    if gpu:
        cmd += ["--gpu", gpu.upper()]
    if high_mem:
        cmd.append("--high-mem")
    if keep:
        cmd.append("--keep")
    cmd.append(str(_local_path(script)))
    cmd += list(args or [])
    return _run(cmd, timeout=timeout)


# --------------------------------------------------------------------------- archivos


@mcp.tool()
@_guard
def colab_upload(local_path: str, remote_path: str, session: str | None = None) -> str:
    """Sube un archivo local (dentro de la carpeta de trabajo) al runtime."""
    local = _local_path(local_path)
    if not local.is_file():
        raise ToolError(f"No existe el archivo local: {local_path}")
    return _run(["upload", *_session_args(session), str(local), remote_path], timeout=600)


@mcp.tool()
@_guard
def colab_download(remote_path: str, local_path: str, session: str | None = None) -> str:
    """Descarga un archivo del runtime a una ruta local dentro de la carpeta de trabajo."""
    local = _local_path(local_path)
    local.parent.mkdir(parents=True, exist_ok=True)
    return _run(["download", *_session_args(session), remote_path, str(local)], timeout=600)


@mcp.tool()
@_guard
def colab_ls(path: str = "content", session: str | None = None) -> str:
    """Lista archivos en el runtime (por defecto /content)."""
    return _run(["ls", *_session_args(session), path], timeout=60)


@mcp.tool()
@_guard
def colab_rm(path: str, session: str | None = None) -> str:
    """Borra UN archivo del runtime (no del equipo del usuario). Rechaza rutas raíz."""
    cleaned = path.strip().rstrip("/")
    if cleaned in {"", ".", "..", "content", "/content", "/"}:
        raise ToolError("Ruta remota demasiado amplia; indica un archivo concreto.")
    return _run(["rm", *_session_args(session), path], timeout=60)


@mcp.tool()
@_guard
def colab_log(session: str | None = None, lines: int = 50, event_type: str | None = None) -> str:
    """Muestra las últimas `lines` líneas del historial de una sesión (event_type: p. ej. execution, file_operation). Sin sesión, lista las que tienen registro."""
    if not 1 <= lines <= 2000:
        raise ToolError("lines debe estar entre 1 y 2000.")
    args = ["log", *_session_args(session), "--lines", str(lines)]
    if event_type:
        if not EVENT_RE.match(event_type):
            raise ToolError("event_type inválido.")
        args += ["--type", event_type]
    return _run(args, timeout=60)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
