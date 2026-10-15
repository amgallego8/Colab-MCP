"""Tests for local cleanup and disk guards (footprint.py). Run: python tests/test_footprint.py"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))


def age(p: Path, seconds: float):
    t = time.time() - seconds
    os.utime(p, (t, t))


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    work = tmp / "work"
    work.mkdir()
    outside = tmp / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("mine")
    os.environ["COLAB_MCP_WORKDIR"] = str(work)
    for k in ("COLAB_MCP_MIN_FREE_GB", "COLAB_MCP_WORKDIR_MAX_GB", "COLAB_MCP_IMAGE_DAYS", "COLAB_MCP_TRASH_DAYS"):
        os.environ.pop(k, None)
    from colab_cli_mcp import footprint as fp
    from colab_cli_mcp.core import ToolError

    tmpd, imgd, trashd = work / ".colab-mcp/tmp", work / ".colab-mcp/images", work / ".colab-mcp-trash"
    for d in (tmpd, imgd, trashd):
        d.mkdir(parents=True)
    old_zip, new_zip = tmpd / "old.zip", tmpd / "new.zip"
    old_img, new_img = imgd / "old.png", imgd / "new.png"
    old_nb, new_nb = trashd / "old.ipynb", trashd / "new.ipynb"
    for f in (old_zip, new_zip, old_img, new_img, old_nb, new_nb):
        f.write_bytes(b"x" * 1000)
    age(old_zip, 7200)
    age(old_img, 3 * 86400)
    age(old_nb, 8 * 86400)
    (work / "mi_cuaderno.ipynb").write_text("{}")
    (work / "datos.csv").write_text("a\n")
    (tmpd / "sub").mkdir()
    (tmpd / "sub" / "old.bin").write_bytes(b"y")
    age(tmpd / "sub" / "old.bin", 7200)
    # a symlink inside a managed folder pointing outside must not be followed or deleted through
    (tmpd / "link").symlink_to(outside)
    (imgd / "flink").symlink_to(outside / "keep.txt")

    r = fp.sweep()
    check("old temp, old plots and old trash are removed", not old_zip.exists() and not old_img.exists() and not old_nb.exists())
    check("fresh temp, plots and trash are kept", new_zip.exists() and new_img.exists() and new_nb.exists())
    check("empty sub-folders are removed", not (tmpd / "sub").exists())
    check("user files are never touched", (work / "mi_cuaderno.ipynb").exists() and (work / "datos.csv").exists())
    check("symlinks are not followed", (outside / "keep.txt").read_text() == "mine" and outside.exists())
    check("report counts removed files", r["tmp"][0] >= 2 and r["images"][0] == 1 and r["trash"][0] == 1, str(r))

    fp.sweep(everything=True)
    check("cleanup now removes fresh temp and plots", not new_zip.exists() and not new_img.exists())
    check("cleanup now keeps recent trash (recoverable)", new_nb.exists())
    check("cleanup never removes user files", (work / "mi_cuaderno.ipynb").exists() and (outside / "keep.txt").exists())

    os.environ["COLAB_MCP_TRASH_DAYS"] = "0"
    age(new_nb, 400 * 86400)
    fp.sweep()
    check("TRASH_DAYS=0 keeps trash forever", new_nb.exists())
    os.environ["COLAB_MCP_TRASH_DAYS"] = "abc"
    check("bad env value falls back to default", fp.trash_days() == 7)
    os.environ.pop("COLAB_MCP_TRASH_DAYS")

    # sweeping a work dir without managed folders must not fail
    empty = tmp / "empty"
    empty.mkdir()
    os.environ["COLAB_MCP_WORKDIR"] = str(empty)
    fp.sweep_quietly()
    check("sweep on a clean directory is a no-op", list(empty.iterdir()) == [])
    os.environ["COLAB_MCP_WORKDIR"] = str(work)

    # guards
    fp.check_space(1024, "test")
    check("small operations pass the guards", True)
    for needle, env, val in (("free disk", "COLAB_MCP_MIN_FREE_GB", str(10**9)), ("budget", "COLAB_MCP_WORKDIR_MAX_GB", "0.000001")):
        os.environ[env] = val
        try:
            fp.check_space(10_000_000, "a download")
            check(f"guard blocks: {needle}", False, "no error")
        except ToolError as exc:
            check(f"guard blocks: {needle}", env in str(exc), str(exc))
        os.environ.pop(env)
    os.environ["COLAB_MCP_WORKDIR_MAX_GB"] = "0"
    fp.check_space(10**12 if fp.free_bytes() > 10**12 + fp.min_free_bytes() else 1, "x")
    check("WORKDIR_MAX_GB=0 disables the budget", True)
    os.environ.pop("COLAB_MCP_WORKDIR_MAX_GB")

    rep = fp.report()
    check("report mentions free disk and budget", "free disk" in rep and "workdir" in rep, rep)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
