"""Tests for mounting Drive from the chat (mount.py), with a simulated CLI that really waits on /dev/tty.
Run: python tests/test_mount.py"""
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FAKE = ROOT / "tests" / "fake_colab.py"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    vm, work = tmp / "vm", tmp / "work"
    vm.mkdir()
    work.mkdir()
    fake = tmp / "colab"
    fake.write_text(f"#!{sys.executable}\n" + FAKE.read_text().split("\n", 1)[1])
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log = tmp / "calls.jsonl"
    os.environ.update({"COLAB_BIN": str(fake), "FAKE_VM_ROOT": str(vm), "COLAB_MCP_WORKDIR": str(work), "FAKE_COLAB_LOG": str(log)})
    for k in ("COLAB_MCP_AUTH", "FAKE_DRIVE_MODE", "FAKE_DRIVE_URI"):
        os.environ.pop(k, None)

    from colab_cli_mcp import mount
    from colab_cli_mcp.core import ToolError

    opened = []
    mount.open_browser = lambda url: opened.append(url) or True
    drive_dir = vm / "content/drive/MyDrive"

    # url safety
    ok = mount.safe_consent_url
    check("accepts https google.com pages", ok("https://accounts.google.com/o/oauth2/x?a=1") and ok("https://colab.research.google.com/tun/m/x"))
    check("rejects other hosts, http, look-alikes and credentials in the url",
          not any(ok(u) for u in ("http://accounts.google.com/x", "https://evil.com/x", "https://google.com.evil.com/x",
                                  "https://notgoogle.com/x", "https://u:p@accounts.google.com/x", "javascript:alert(1)", "")))

    # validation
    for bad in (None, "", "a b", "x" * 41, "../x", "a;b"):
        try:
            mount.start(bad)
            check(f"start rejects session {bad!r}", False)
        except ToolError:
            check(f"start rejects session {bad!r}", True)
    try:
        mount.finish("ydl")
        check("finish without start is refused", False)
    except ToolError as exc:
        check("finish without start is refused", "No Drive mount is waiting" in str(exc), str(exc))

    # happy path: URL is opened, nothing mounted until finish, Enter makes it complete
    out = mount.start("ydl")
    check("start opens the approval page, not the other links the CLI prints", opened == ["https://accounts.google.com/o/oauth2/fake"], str(opened))
    check("start does not repeat the full link in its answer", "o/oauth2/fake" in out and "login_hint" not in out, out)
    check("start does not mount anything by itself", not drive_dir.exists())
    try:
        mount.start("ydl")
        check("a second start on the same session is refused while pending", False)
    except ToolError as exc:
        check("a second start on the same session is refused while pending", "already waiting" in str(exc), str(exc))
    out = mount.finish("ydl")
    check("finish presses Enter and the mount completes", drive_dir.is_dir() and "Credentials propagated" in out, out)
    check("the CLI was called as drivemount -s ydl (argument list, no shell)",
          any(c.get("argv") == ["drivemount", "-s", "ydl"] for c in map(__import__("json").loads, log.read_text().splitlines())))
    try:
        mount.finish("ydl")
        check("finish twice is refused", False)
    except ToolError:
        check("finish twice is refused", True)

    # the link carries the account e-mail: never returned unless needed
    shutil.rmtree(vm / "content", ignore_errors=True)
    opened.clear()
    os.environ["FAKE_DRIVE_URI"] = "https://accounts.google.com/o/oauth2/v2/auth?login_hint=persona%40example.com&state=abc123"
    out = mount.start("ydl")
    check("the e-mail and state in the link are not returned", "persona" not in out and "abc123" not in out and "auth?..." in out, out)
    check("but the browser got the full link", opened == [os.environ["FAKE_DRIVE_URI"]], str(opened))
    out = mount.finish("ydl")
    check("finish output hides the link too", "persona" not in out and "abc123" not in out, out)
    shutil.rmtree(vm / "content", ignore_errors=True)
    mount.open_browser = lambda url: False
    out = mount.start("ydl")
    check("if the browser cannot open, the full link IS returned", "persona%40example.com" in out, out)
    mount.finish("ydl", cancel=True)
    mount.open_browser = lambda url: opened.append(url) or True
    out = mount.start("ydl", show_link=True)
    check("show_link=true returns the full link", "persona%40example.com" in out, out)
    mount.finish("ydl", cancel=True)
    os.environ.pop("FAKE_DRIVE_URI")
    opened.clear()
    check("redact hides query and fragment", mount.redact("see https://a.google.com/x?y=1&z=2 and https://b.google.com/p#frag ok") == "see https://a.google.com/x?... and https://b.google.com/p?... ok")

    # a link that arrives in pieces is opened whole, never cut
    shutil.rmtree(vm / "content", ignore_errors=True)
    opened.clear()
    os.environ["FAKE_DRIVE_URI"] = "https://accounts.google.com/o/oauth2/v2/auth?client_id=abc&scope=drive&state=xyz"
    os.environ["FAKE_DRIVE_SLOW"] = "1"
    mount.start("ydl")
    check("a link delivered in two pieces is opened complete", opened == [os.environ["FAKE_DRIVE_URI"]], str(opened))
    mount.finish("ydl", cancel=True)
    os.environ.pop("FAKE_DRIVE_SLOW")
    os.environ.pop("FAKE_DRIVE_URI")

    # cancel
    shutil.rmtree(vm / "content", ignore_errors=True)
    opened.clear()
    mount.start("ydl")
    out = mount.finish("ydl", cancel=True)
    check("cancel stops the process and mounts nothing", "Cancelled" in out and not drive_dir.exists(), out)

    # already authorised: no prompt, the command ends by itself
    os.environ["FAKE_DRIVE_MODE"] = "authorized"
    opened.clear()
    out = mount.start("ydl")
    check("no approval needed: reports it and mounted", "No approval was needed" in out and drive_dir.is_dir() and not opened, out)
    shutil.rmtree(vm / "content", ignore_errors=True)

    # failures
    os.environ["FAKE_DRIVE_MODE"] = "fail"
    try:
        mount.start("ydl")
        check("a failing command is reported", False)
    except ToolError as exc:
        check("a failing command is reported", "exit code 1" in str(exc) and "boom" in str(exc), str(exc))

    os.environ["FAKE_DRIVE_MODE"] = "consent"
    os.environ["FAKE_DRIVE_URI"] = "https://evil.example.com/steal"
    opened.clear()
    try:
        mount.start("ydl")
        check("a link outside google.com is never opened", False)
    except ToolError as exc:
        check("a link outside google.com is never opened", not opened and "not an https page on google.com" in str(exc), str(exc))
    os.environ.pop("FAKE_DRIVE_URI")

    os.environ["COLAB_BIN"] = "/no/such/colab"
    try:
        mount.start("ydl")
        check("missing CLI gives a clear error", False)
    except ToolError as exc:
        check("missing CLI gives a clear error", "not found" in str(exc), str(exc))

    bad = [n for n, c in results if not c]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
