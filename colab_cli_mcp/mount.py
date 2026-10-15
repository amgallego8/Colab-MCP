"""Mount Google Drive on a Colab VM from inside the chat flow (no terminal, no Enter key).

The official `colab drivemount` is interactive: it prints "Please visit: <URL>" and then waits for a
key press on /dev/tty. It never opens a browser, which is why people run it and "nothing happens".

Here the same command runs in a pseudo-terminal that we own:
  1. start(): launch it, read the consent URL from its output, open it in the user's browser (and
     return it, in case the browser does not open).
  2. The USER clicks Allow in Google's own page. We never see credentials or codes.
  3. finish(): send the Enter key the CLI is waiting for, wait for it to end, and verify the mount.

Safety: only https URLs on google.com hosts are opened; the process is killed after a time limit;
at most one pending mount per session; nothing from the CLI output is returned except a short,
cleaned tail.
"""

import os
import re
import subprocess
import threading
import time
import webbrowser
from urllib.parse import urlparse

from .core import SESSION_RE, ToolError, colab_bin, strip_ansi

URL_RE = re.compile(r"https://[^\s\"'<>]+")
MAX_LIFETIME_SEC = 15 * 60
START_WAIT_SEC = 90
FINISH_WAIT_SEC = 150

_PENDING: dict[str, "Pending"] = {}
_LOCK = threading.Lock()


def safe_consent_url(url: str) -> bool:
    """Only open https pages on google.com (and its subdomains)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "google.com" or host.endswith(".google.com")) and not parsed.username


def open_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:
        return False


class Pending:
    """One running `colab drivemount` attached to a pseudo-terminal."""

    def __init__(self, session: str):
        import pty  # imported here so the module still loads on platforms without it

        self.session = session
        self.output = ""
        self.url: str | None = None
        self.done = threading.Event()
        self.closed = False
        self.started = time.time()
        auth = os.environ.get("COLAB_MCP_AUTH")
        cmd = [colab_bin()] + (["--auth", auth] if auth else []) + ["drivemount", "-s", session]
        self.master, slave = pty.openpty()
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=slave, stdout=slave, stderr=slave, close_fds=True,
                start_new_session=True, preexec_fn=_take_controlling_tty,
            )
        except FileNotFoundError:
            os.close(self.master)
            os.close(slave)
            raise ToolError(
                f"Executable '{colab_bin()}' not found. Install the CLI (`uv tool install google-colab-cli`) "
                "or set COLAB_BIN to its path."
            ) from None
        os.close(slave)
        threading.Thread(target=self._read, daemon=True).start()
        self.timer = threading.Timer(MAX_LIFETIME_SEC, self.kill)
        self.timer.daemon = True
        self.timer.start()

    def _read(self):
        while True:
            try:
                chunk = os.read(self.master, 4096)
            except OSError:
                break
            if not chunk:
                break
            self.output = (self.output + chunk.decode("utf-8", "replace"))[-20000:]
            if self.url is None:
                for m in URL_RE.finditer(strip_ansi(self.output).replace("\r", "")):
                    self.url = m.group(0).rstrip(".,)")
                    break
        self.proc.wait()
        self.done.set()

    def press_enter(self):
        try:
            os.write(self.master, b"\n")
        except OSError:
            pass

    def kill(self):
        self.timer.cancel()
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, 15)
            except OSError:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.proc.pid, 9)
                except OSError:
                    pass
        self.done.wait(timeout=5)
        self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                os.close(self.master)
            except OSError:
                pass

    def tail(self, n: int = 6) -> str:
        text = strip_ansi(self.output).replace("\r", "")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[-n:])


def _take_controlling_tty():  # runs in the child between fork and exec
    import fcntl
    import termios

    try:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except OSError:
        pass


def _session(session: str | None) -> str:
    if not session or not SESSION_RE.match(session):
        raise ToolError("Give the session name (letters, digits, '-' and '_' only, max 40).")
    return session


def start(session: str | None) -> str:
    name = _session(session)
    with _LOCK:
        old = _PENDING.get(name)
        if old is not None and not old.done.is_set() and time.time() - old.started < MAX_LIFETIME_SEC:
            raise ToolError(
                f"A Drive mount is already waiting for approval on '{name}'. Call colab_drive_mount_finish "
                "after the user clicked Allow, or colab_drive_mount_finish with cancel=true to start over."
            )
        if old is not None:
            old.kill()
        pending = Pending(name)
        _PENDING[name] = pending
    deadline = time.time() + START_WAIT_SEC
    while time.time() < deadline and pending.url is None and not pending.done.is_set():
        time.sleep(0.2)
    if pending.url is None:
        if pending.done.is_set():
            _PENDING.pop(name, None)
            code = pending.proc.returncode
            body = pending.tail()
            if code == 0:
                return f"No approval was needed: the mount command finished on its own.\n{body}".strip()
            raise ToolError(f"The mount command ended (exit code {code}) before giving an approval link.\n{body}")
        pending.kill()
        _PENDING.pop(name, None)
        raise ToolError(f"No approval link appeared within {START_WAIT_SEC} s.\n{pending.tail()}")
    if not safe_consent_url(pending.url):
        pending.kill()
        _PENDING.pop(name, None)
        raise ToolError("The approval link was not an https page on google.com, so it was not opened. Nothing was mounted.")
    opened = open_browser(pending.url)
    return (
        ("Opened the Google approval page in the browser." if opened else "Could not open a browser from here.")
        + f"\nIf nothing appears, open this link yourself:\n{pending.url}\n"
        "The user must click Allow. This gives code running on the VM access to the WHOLE Drive "
        "(stop the session when done). When the user says it is done, call colab_drive_mount_finish."
    )


def finish(session: str | None, cancel: bool = False) -> str:
    name = _session(session)
    with _LOCK:
        pending = _PENDING.pop(name, None)
    if pending is None:
        raise ToolError(f"No Drive mount is waiting on '{name}'. Call colab_drive_mount_start first.")
    if cancel:
        pending.kill()
        return "Cancelled. Nothing was mounted."
    if not pending.done.is_set():
        pending.press_enter()
        pending.done.wait(timeout=FINISH_WAIT_SEC)
    if not pending.done.is_set():
        pending.kill()
        raise ToolError(f"The mount did not finish within {FINISH_WAIT_SEC} s and was stopped.\n{pending.tail()}")
    pending.close()
    code = pending.proc.returncode
    body = pending.tail()
    if code != 0:
        raise ToolError(f"The mount command failed (exit code {code}).\n{body}")
    return f"The mount command finished.\n{body}\nVerify with colab_drive_status."

