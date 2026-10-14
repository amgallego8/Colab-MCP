"""Core helpers: configuration, validation, and the single place that runs the `colab` CLI.

Nothing here is MCP-specific, so everything can be unit-tested without a server.

Safety properties enforced here:
- The CLI is always run with an argument list, never through a shell.
- Local paths are confined to COLAB_MCP_WORKDIR (symlinks resolved).
- Secret values from COLAB_MCP_ENV_FILE are scrubbed from everything returned.
- Output is clipped to COLAB_MCP_MAX_OUTPUT characters.
"""

import functools
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-\[\],<>=!~+]*$")
JOB_RE = re.compile(r"^[0-9]{6,12}-[0-9a-f]{6}$")
EVENT_RE = re.compile(r"^[a-z_]{1,40}$")
GPUS = {"T4", "L4", "G4", "H100", "A100"}
TPUS = {"v5e1", "v6e1"}


def colab_bin() -> str:
    return os.environ.get("COLAB_BIN", "colab")


def max_output() -> int:
    return int(os.environ.get("COLAB_MCP_MAX_OUTPUT", "20000"))


def max_upload_bytes() -> int:
    # The CLI uploads a file in a single request; we cap conservatively (override if you know better).
    return int(float(os.environ.get("COLAB_MCP_MAX_UPLOAD_MB", "100")) * 1024 * 1024)


def workdir() -> Path:
    return Path(os.environ.get("COLAB_MCP_WORKDIR") or Path.cwd()).resolve()


class ToolError(Exception):
    """Invalid input. Returned to the model as text; nothing is executed."""


def guard(fn):
    """Turn ToolError into an `ERROR: ...` string while keeping the function signature for FastMCP."""

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except ToolError as exc:
            return f"ERROR: {exc}"

    return wrapper


# ------------------------------------------------------------------ validation


def session_args(session: str | None) -> list[str]:
    if session is None:
        return []
    if not SESSION_RE.match(session):
        raise ToolError("Invalid session name: use letters, digits, '-' and '_' only (max 40).")
    return ["-s", session]


def local_path(path: str) -> Path:
    """Resolve `path` inside the working directory; reject anything that escapes it."""
    root = workdir()
    p = Path(path)
    resolved = (p if p.is_absolute() else root / p).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ToolError(f"Local path is outside the allowed working directory ({root}).") from None
    return resolved


def check_gpu(gpu: str | None) -> str | None:
    if gpu is None:
        return None
    if gpu.upper() not in GPUS:
        raise ToolError(f"Invalid gpu. Options: {', '.join(sorted(GPUS))}.")
    return gpu.upper()


# ------------------------------------------------------------------ secrets / output


def load_secrets() -> dict[str, str]:
    """Read KEY=VALUE pairs from COLAB_MCP_ENV_FILE (re-read on every call)."""
    env_file = os.environ.get("COLAB_MCP_ENV_FILE")
    if not env_file:
        return {}
    path = Path(env_file).expanduser()
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


def scrub(text: str, secrets: dict[str, str] | None = None) -> str:
    for value in (secrets if secrets is not None else load_secrets()).values():
        if len(value) >= 4:
            text = text.replace(value, "***")
    return text


def clip(text: str, limit: int | None = None) -> str:
    limit = limit or max_output()
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n... [output clipped: {len(text) - limit} characters] ...\n{text[-tail:]}"


# ------------------------------------------------------------------ running the CLI


@dataclass
class Result:
    returncode: int  # -1 = could not run / timed out
    stdout: str
    stderr: str
    problem: str = ""  # human-readable reason when the CLI could not run

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.problem

    def combined(self) -> str:
        body = self.stdout
        if self.stderr:
            body = (body + "\n" + self.stderr) if body else self.stderr
        return body.strip()


def run_colab_raw(args: list[str], *, stdin: str | None = None, timeout: float = 120.0) -> Result:
    secrets = load_secrets()
    auth = os.environ.get("COLAB_MCP_AUTH")
    cmd = [colab_bin()] + (["--auth", auth] if auth else []) + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workdir()),
            **({"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}),
        )
    except FileNotFoundError:
        return Result(
            -1, "", "",
            f"Executable '{colab_bin()}' not found. Install the CLI (`uv tool install google-colab-cli`) "
            "or set COLAB_BIN to its path.",
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return Result(-1, scrub(partial, secrets), "", f"Timed out after {timeout:.0f} s.")
    return Result(proc.returncode, scrub(proc.stdout or "", secrets), scrub(proc.stderr or "", secrets))


LOGIN_HINT = (
    "\n\nLikely cause: Google sign-in missing or expired. Run `colab sessions` once in a terminal to sign in."
)


def format_result(res: Result) -> str:
    """Render a Result for the model."""
    if res.problem:
        return clip(f"ERROR: {res.problem}\n{res.stdout}".strip())
    body = clip(res.combined())
    if res.returncode == 0:
        return body or "OK"
    low = body.lower()
    hint = LOGIN_HINT if any(k in low for k in ("credential", "login", "token", "401", "403", "authoriz")) else ""
    return f"ERROR (exit code {res.returncode}):\n{body}{hint}"


def run_colab(args: list[str], *, stdin: str | None = None, timeout: float = 120.0) -> str:
    return format_result(run_colab_raw(args, stdin=stdin, timeout=timeout))
