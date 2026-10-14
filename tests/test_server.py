"""End-to-end tests: a real MCP client over stdio against the server, with a fake `colab`.

Run:  python tests/test_server.py
"""
import asyncio
import json
import os
import stat
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FAKE = ROOT / "tests" / "fake_colab.py"
SECRET = "sk-or-SECRET-1234567890"

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))


def text(res) -> str:
    return "".join(c.text for c in res.content if getattr(c, "type", "") == "text")


def calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


EXPECTED_TOOLS = {
    "colab_sessions", "colab_status", "colab_usage", "colab_new", "colab_ensure_session", "colab_restart_kernel",
    "colab_url", "colab_runtime_info", "colab_stop", "colab_install", "colab_exec", "colab_run",
    "colab_job_start", "colab_job_status", "colab_job_logs", "colab_job_cancel", "colab_job_list",
    "colab_notebook_create", "colab_notebook_list", "colab_notebook_read", "colab_notebook_add_cell",
    "colab_notebook_edit_cell", "colab_notebook_delete_cell", "colab_notebook_move_cell",
    "colab_notebook_clear_outputs", "colab_notebook_run_cell", "colab_notebook_run_all",
    "colab_notebook_to_script", "colab_notebook_delete", "colab_upload", "colab_download",
    "colab_upload_dir", "colab_download_dir", "colab_ls", "colab_rm", "colab_log",
}


async def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    work, vm = tmp / "work", tmp / "vm"
    work.mkdir()
    vm.mkdir()
    (work / "datos.csv").write_text("a,b\n1,2\n")
    (work / "job.py").write_text("print(1)\n")
    outside = tmp / "outside.txt"
    outside.write_text("must not leave")
    (work / "link").symlink_to(outside)
    envfile = tmp / "secrets.env"
    envfile.write_text(f"# comment\nOPENROUTER_API_KEY={SECRET}\nOTHER='quoted-value-99'\n")
    log = tmp / "calls.jsonl"
    fake = tmp / "colab"
    fake.write_text(f"#!{sys.executable}\n" + FAKE.read_text().split("\n", 1)[1])
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    env = {
        **os.environ,
        "COLAB_BIN": str(fake),
        "COLAB_MCP_WORKDIR": str(work),
        "COLAB_MCP_ENV_FILE": str(envfile),
        "FAKE_COLAB_LOG": str(log),
        "FAKE_VM_ROOT": str(vm),
        "COLAB_MCP_JOB_ROOT": "/content/.colab_mcp",
        "COLAB_MCP_MAX_OUTPUT": "3000",
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "colab_cli_mcp.server"], env=env, cwd=str(ROOT))

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name: t for t in (await s.list_tools()).tools}
            check("exposes exactly the expected tools", set(tools) == EXPECTED_TOOLS, str(set(tools) ^ EXPECTED_TOOLS))
            check("every tool has a description", all((t.description or "").strip() for t in tools.values()))
            check("colab_exec schema has its parameters",
                  {"code", "file", "session", "timeout", "secrets", "plot_path"} <= set(tools["colab_exec"].inputSchema["properties"]))

            async def call(_tool_name, **kw):
                return text(await s.call_tool(_tool_name, kw))

            # ---------------------------------------------------------------- exec + secrets
            out = await call("colab_exec", code="print('x')", session="trainer")
            c = calls(log)[-1]
            check("exec sends code over stdin with -s and --timeout",
                  c["stdin"] == "print('x')" and c["argv"][:3] == ["exec", "-s", "trainer"] and "--timeout" in c["argv"], str(c))
            check("exec returns the output", out.strip() == "x", out)

            out = await call("colab_exec", code="import os; print('key=' + os.environ['OPENROUTER_API_KEY'])", secrets=["OPENROUTER_API_KEY"])
            c = calls(log)[-1]
            check("secret reaches the CLI via --env", f"OPENROUTER_API_KEY={SECRET}" in c["argv"], str(c["argv"]))
            check("secret value is scrubbed from the result", SECRET not in out and "key=***" in out, out)
            out = await call("colab_exec", code="1", secrets=["NOPE"])
            check("unknown secret name is rejected and nothing runs", out.startswith("ERROR") and "NOPE" in out and SECRET not in out, out)
            out = await call("colab_exec", code="a", file="job.py")
            check("exec requires code XOR file", out.startswith("ERROR"), out)
            await call("colab_exec", file="job.py")
            check("exec file uses -f with a resolved path", calls(log)[-1]["argv"][-2:] == ["-f", str(work / "job.py")])
            out = await call("colab_exec", code="FAKE_PLOT = 1", plot_path="plots/p.png")
            check("plot_path is passed as --output-image and the image lands in the workdir", (work / "plots" / "p.png").is_file(), out)

            # ---------------------------------------------------------------- local path confinement
            n = len(calls(log))
            for label, tool, kw in [
                ("upload ../", "colab_upload", dict(local_path="../outside.txt", remote_path="/content/x")),
                ("upload absolute outside", "colab_upload", dict(local_path=str(outside), remote_path="/content/x")),
                ("upload through symlink", "colab_upload", dict(local_path="link", remote_path="/content/x")),
                ("download to ../", "colab_download", dict(remote_path="/content/a", local_path="../stolen.txt")),
                ("exec -f outside", "colab_exec", dict(file=str(outside))),
                ("run script outside", "colab_run", dict(script="../outside.txt")),
                ("notebook outside", "colab_notebook_create", dict(path="../evil.ipynb")),
                ("upload_dir outside", "colab_upload_dir", dict(local_dir="..", remote_dir="/content/x")),
                ("download_dir to outside", "colab_download_dir", dict(remote_dir="/content/x", local_dir="../out")),
                ("to_script outside", "colab_notebook_to_script", dict(path="a.ipynb", out_path="../x.py")),
            ]:
                out = await call(tool, **kw)
                check(f"rejects: {label}", out.startswith("ERROR"), out)
            check("rejected calls never reached the CLI", len(calls(log)) == n, str(calls(log)[n:]))
            check("nothing written outside the workdir", not (tmp / "stolen.txt").exists() and not (tmp / "evil.ipynb").exists() and not (tmp / "out").exists())

            # ---------------------------------------------------------------- plain files
            await call("colab_upload", local_path="datos.csv", remote_path="/content/datos.csv")
            check("upload stores the file on the (fake) VM", (vm / "content" / "datos.csv").read_text() == "a,b\n1,2\n")
            out = await call("colab_download", remote_path="/content/datos.csv", local_path="out/d.csv")
            check("download creates sub-folders", (work / "out" / "d.csv").read_text() == "a,b\n1,2\n", out)
            out = await call("colab_download", remote_path="/content/missing.txt", local_path="m.txt")
            check("download of a missing file reports an error", out.startswith("ERROR"), out)
            out = await call("colab_ls")
            check("ls lists VM files", "datos.csv" in out, out)
            await call("colab_rm", path="/content/datos.csv")
            check("rm removes one file", not (vm / "content" / "datos.csv").exists())

            # ---------------------------------------------------------------- input validation
            n = len(calls(log))
            for label, tool, kw in [
                ("session with spaces/options", "colab_status", dict(session="x --auth adc")),
                ("bad gpu", "colab_new", dict(gpu="RTX9090")),
                ("gpu and tpu", "colab_new", dict(gpu="T4", tpu="v5e1")),
                ("package as option", "colab_install", dict(packages=["--index-url=http://evil"])),
                ("rm root", "colab_rm", dict(path="/")),
                ("rm /content/", "colab_rm", dict(path="/content/")),
                ("log out of range", "colab_log", dict(lines=0)),
                ("absurd timeout", "colab_exec", dict(code="1", timeout=99999)),
                ("bad job id", "colab_job_status", dict(job_id="../../etc/passwd")),
                ("job tail out of range", "colab_job_logs", dict(job_id="123456-abcdef", tail=0)),
            ]:
                out = await call(tool, **kw)
                check(f"validates: {label}", out.startswith("ERROR"), out)
            check("invalid input never reached the CLI", len(calls(log)) == n, str(calls(log)[n:]))

            # ---------------------------------------------------------------- sessions
            await call("colab_new", session="lab", gpu="t4", high_mem=True)
            check("new builds --gpu T4 --high-mem", calls(log)[-1]["argv"] == ["new", "-s", "lab", "--gpu", "T4", "--high-mem"], str(calls(log)[-1]))
            out = await call("colab_ensure_session", session="lab", gpu="T4")
            check("ensure_session reuses an existing session", "already exists" in out, out)
            n = len(calls(log))
            out = await call("colab_ensure_session", session="fresh", gpu="L4")
            check("ensure_session creates a missing one", "created it" in out and [c["argv"][0] for c in calls(log)[n:]] == ["status", "new"], out)
            await call("colab_install", packages=["openai", "pandas>=2.0", "pillow"])
            check("install passes the packages", calls(log)[-1]["argv"] == ["install", "openai", "pandas>=2.0", "pillow"])
            out = await call("colab_log", session="lab", lines=3, event_type="execution")
            check("log passes --lines and --type", calls(log)[-1]["argv"] == ["log", "-s", "lab", "--lines", "3", "--type", "execution"] and "evento 2" in out)
            await call("colab_restart_kernel", session="lab")
            check("restart_kernel maps to restart-kernel", calls(log)[-1]["argv"] == ["restart-kernel", "-s", "lab"])
            await call("colab_url", session="lab")
            check("url maps to url", calls(log)[-1]["argv"] == ["url", "-s", "lab"])
            await call("colab_stop", session="lab")
            check("stop", calls(log)[-1]["argv"] == ["stop", "-s", "lab"])
            await call("colab_run", script="job.py", args=["--n", "3"], gpu="L4", keep=True)
            check("run builds flags, script and args", calls(log)[-1]["argv"] == ["run", "--gpu", "L4", "--keep", str(work / "job.py"), "--n", "3"])
            out = await call("colab_runtime_info")
            info = json.loads(out)
            check("runtime_info returns structured data", "python" in info and "cpus" in info, out)

            # ---------------------------------------------------------------- notebooks
            out = await call("colab_notebook_create", path="exp/q.ipynb", cells=[
                {"type": "markdown", "source": "# Test"},
                {"type": "code", "source": "x = 21\nprint(x * 2)"},
                {"type": "code", "source": "raise ValueError('boom')"},
                {"type": "code", "source": "print('after')"},
            ])
            check("notebook_create makes a valid file", "4 cell(s)" in out and (work / "exp" / "q.ipynb").is_file(), out)
            out = await call("colab_notebook_create", path="exp/q.ipynb")
            check("notebook_create refuses to overwrite by default", out.startswith("ERROR") and "overwrite" in out, out)
            out = await call("colab_notebook_list")
            check("notebook_list finds it", "exp/q.ipynb" in out and "cells: 4" in out, out)
            read1 = await call("colab_notebook_read", path="exp/q.ipynb")
            ids = [l.split("id=")[1].split(" ")[0] for l in read1.splitlines() if l.startswith("--- cell")]
            check("notebook_read shows cell ids", len(ids) == 4 and "x = 21" in read1, read1)

            out = await call("colab_notebook_run_cell", path="exp/q.ipynb", cell=ids[1])
            check("run_cell reports ok and the printed value", "status=ok" in out and "42" in out, out)
            out = await call("colab_notebook_run_cell", path="exp/q.ipynb", cell="1")
            check("run_cell accepts an index too", "status=ok" in out, out)
            out = await call("colab_notebook_run_cell", path="exp/q.ipynb", cell=ids[2])
            check("run_cell reports ERROR for a failing cell", "status=ERROR" in out and "boom" in out, out)
            saved = await call("colab_notebook_read", path="exp/q.ipynb")
            check("outputs (stream and error) are stored in the notebook", "[stdout] 42" in saved and "[error] ValueError: boom" in saved, saved)
            out = await call("colab_notebook_run_cell", path="exp/q.ipynb", cell="0")
            check("running a markdown cell is rejected", out.startswith("ERROR"), out)
            await call("colab_notebook_add_cell", path="exp/q.ipynb", source="print('FAKE_PLOT')", position=4)
            out = await call("colab_notebook_run_cell", path="exp/q.ipynb", cell="4")
            check("a displayed plot is saved and referenced", "[plot saved:" in out and any((work / ".colab-mcp" / "images").glob("q-*.png")), out)

            out = await call("colab_notebook_run_all", path="exp/q.ipynb")
            check("run_all stops at the first failing cell by default", "1 failed" in out and "Stopped at cell 2" in out and "after" not in out.split("Stopped")[0].split("[cell 3")[-1], out)
            out = await call("colab_notebook_run_all", path="exp/q.ipynb", stop_on_error=False)
            check("run_all with stop_on_error=false continues", "Ran 4 cell(s); 1 failed" in out and "after" in out, out)

            out = await call("colab_notebook_edit_cell", path="exp/q.ipynb", cell=ids[1], source="print('edited')")
            r2 = await call("colab_notebook_read", path="exp/q.ipynb")
            check("edit_cell replaces source and clears stale outputs", "print('edited')" in r2 and "[stdout] 42" not in r2, r2)
            await call("colab_notebook_move_cell", path="exp/q.ipynb", cell=ids[3], to_index=0)
            r3 = await call("colab_notebook_read", path="exp/q.ipynb", include_outputs=False)
            first = [l for l in r3.splitlines() if l.startswith("--- cell")][0]
            check("move_cell reorders", f"id={ids[3]}" in first, first)
            await call("colab_notebook_delete_cell", path="exp/q.ipynb", cell=ids[3])
            out = await call("colab_notebook_clear_outputs", path="exp/q.ipynb")
            check("clear_outputs works", "Cleared" in out, out)
            out = await call("colab_notebook_edit_cell", path="exp/q.ipynb", cell="ghost", source="1")
            check("unknown cell id gives a helpful error", out.startswith("ERROR") and "colab_notebook_read" in out, out)

            script = await call("colab_notebook_to_script", path="exp/q.ipynb")
            check("to_script comments markdown and drops nothing else", "# # Test" in script and "raise ValueError" in script, script)
            await call("colab_notebook_add_cell", path="exp/q.ipynb", source="!pip install qiskit\n%matplotlib inline\nimport math", position=0)
            script = await call("colab_notebook_to_script", path="exp/q.ipynb", out_path="exp/q.py")
            body = (work / "exp" / "q.py").read_text()
            check("to_script comments out ! and % lines", "# !pip install qiskit" in body and "# %matplotlib inline" in body and "\nimport math" in "\n" + body, body)

            out = await call("colab_notebook_delete", path="exp/q.ipynb")
            check("notebook_delete moves it to the trash folder", not (work / "exp" / "q.ipynb").exists() and any((work / ".colab-mcp-trash").glob("*q.ipynb")), out)
            out = await call("colab_notebook_list")
            check("trashed notebooks are not listed", "No notebooks" in out, out)

            bad = work / "bad.ipynb"
            bad.write_text("{not json")
            out = await call("colab_notebook_read", path="bad.ipynb")
            check("a corrupt notebook gives an error, not a crash", out.startswith("ERROR"), out)
            out = await call("colab_notebook_read", path="notes.txt")
            check("non-ipynb paths are rejected", out.startswith("ERROR") and ".ipynb" in out, out)

            # a CLI-level failure (here: the CLI times out) must leave the notebook untouched
            await call("colab_notebook_create", path="s.ipynb", cells=[{"type": "code", "source": "print(1)"}])
            before = (work / "s.ipynb").read_text()
            out = await call("colab_notebook_run_cell", path="s.ipynb", cell="0", session="ghost")
            check("a CLI failure is reported and the notebook is not modified",
                  out.startswith("ERROR") and "ghost" in out and (work / "s.ipynb").read_text() == before, out)

            # ---------------------------------------------------------------- background jobs
            out = await call("colab_job_start", command="echo hello-job; sleep 0.2; echo done-job", name="demo")
            job = out.split("job ")[1].split(" ")[0]
            check("job_start returns an id", len(job) > 8, out)
            time.sleep(1.2)
            out = await call("colab_job_status", job_id=job)
            st = json.loads(out)
            check("job finishes with exit code 0", st["state"] == "finished" and st["exit_code"] == 0 and st["name"] == "demo", out)
            out = await call("colab_job_logs", job_id=job, tail=10)
            check("job_logs returns the output", "hello-job" in out and "done-job" in out, out)
            out = await call("colab_job_start", command="exit 3")
            j2 = out.split("job ")[1].split(" ")[0]
            time.sleep(0.8)
            check("a failing job reports its exit code", json.loads(await call("colab_job_status", job_id=j2))["exit_code"] == 3)
            out = await call("colab_job_start", command="sleep 30")
            j3 = out.split("job ")[1].split(" ")[0]
            check("a long job is reported as running", json.loads(await call("colab_job_status", job_id=j3))["state"] == "running")
            out = await call("colab_job_cancel", job_id=j3)
            check("cancel marks it cancelled", json.loads(out)["state"] == "cancelled", out)
            out = await call("colab_job_list")
            check("job_list shows all jobs", out.count('"id"') == 3, out)
            out = await call("colab_job_start", command="echo \"it's $HOME\" 'q'")
            j4 = out.split("job ")[1].split(" ")[0]
            time.sleep(0.8)
            lg = await call("colab_job_logs", job_id=j4)
            check("quotes in commands survive intact", "it's" in lg, lg)
            out = await call("colab_job_status", job_id="999999-abcdef")
            check("unknown job id gives an error", out.startswith("ERROR"), out)

            # ---------------------------------------------------------------- directory transfer
            proj = work / "proj"
            (proj / "src").mkdir(parents=True)
            (proj / "src" / "a.py").write_text("print('a')")
            (proj / "data.txt").write_text("hi")
            (proj / ".env").write_text("OPENROUTER_API_KEY=" + SECRET)
            (proj / "cert.pem").write_text("-----BEGIN-----")
            (proj / ".git").mkdir()
            (proj / ".git" / "config").write_text("x")
            out = await call("colab_upload_dir", local_dir="proj", remote_dir="/content/proj")
            check("upload_dir extracts on the VM", (vm / "content" / "proj" / "src" / "a.py").read_text() == "print('a')" and (vm / "content" / "proj" / "data.txt").is_file(), out)
            check("upload_dir skips secrets and .git", not (vm / "content" / "proj" / ".env").exists() and not (vm / "content" / "proj" / "cert.pem").exists()
                  and not (vm / "content" / "proj" / ".git").exists() and "Skipped 2" in out, out)
            check("upload_dir leaves no temp zip behind", not list((work / ".colab-mcp" / "tmp").glob("*.zip")) and not list((vm / "content" / ".colab_mcp" / "tmp").glob("*.zip")))
            (vm / "content" / "results").mkdir(parents=True)
            (vm / "content" / "results" / "r.csv").write_text("1,2")
            out = await call("colab_download_dir", remote_dir="/content/results", local_dir="got")
            check("download_dir brings the folder back", (work / "got" / "r.csv").read_text() == "1,2", out)
            check("download_dir leaves no temp zip behind", not list((work / ".colab-mcp" / "tmp").glob("*.zip")) and not list((vm / "content" / ".colab_mcp" / "tmp").glob("*.zip")))
            out = await call("colab_download_dir", remote_dir="/content/nope", local_dir="got2")
            check("download_dir of a missing folder errors cleanly", out.startswith("ERROR"), out)

            # zip-slip: a hostile archive prepared on the "VM" must not escape
            evil = vm / "content" / ".colab_mcp" / "tmp" / "evil.zip"
            evil.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(evil, "w") as z:
                z.writestr("../../escaped.txt", "pwn")
            from colab_cli_mcp import server as srv  # noqa: E402 (direct call to reuse the validation)
            os.environ.update({k: env[k] for k in ("COLAB_BIN", "COLAB_MCP_WORKDIR", "COLAB_MCP_ENV_FILE", "FAKE_COLAB_LOG", "FAKE_VM_ROOT")})
            orig = srv.vm.run_snippet
            srv.vm.run_snippet = lambda *a, **k: ({"zip": "/content/.colab_mcp/tmp/evil.zip", "files": 1, "bytes": 3}, None) if a[1] == srv.vm.ZIP_DIR else orig(*a, **k)
            res = srv.colab_download_dir(remote_dir="/content/x", local_dir="evil_out")
            srv.vm.run_snippet = orig
            check("download_dir refuses zip-slip archives", res.startswith("ERROR") and "unsafe" in res and not (tmp / "escaped.txt").exists() and not (work.parent / "escaped.txt").exists(), res)

    # ---------------------------------------------------------------- direct module checks
    sys.path.insert(0, str(ROOT))
    os.environ["COLAB_BIN"] = str(fake)
    from colab_cli_mcp import core

    clipped = core.clip("x" * 100000, 3000)
    check("clips huge output", len(clipped) < 3300 and "clipped" in clipped, str(len(clipped)))
    res = core.run_colab_raw(["exec"], stdin="FAKE_SLEEP = 1", timeout=1)
    check("a subprocess that exceeds its timeout is reported without hanging", res.problem.startswith("Timed out"), str(res))
    os.environ["FAKE_COLAB_MODE"] = "needs_login"
    out = core.run_colab(["sessions"])
    check("login errors suggest signing in", out.startswith("ERROR") and "sign" in out.lower(), out)
    os.environ.pop("FAKE_COLAB_MODE")
    os.environ["COLAB_BIN"] = "/no/such/colab"
    check("missing CLI explains how to install it", "uv tool install google-colab-cli" in core.run_colab(["sessions"]))

    failed = [x for x in results if not x[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
