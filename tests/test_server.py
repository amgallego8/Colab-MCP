"""Pruebas de extremo a extremo: cliente MCP real por stdio contra el servidor,
con un `colab` falso. Ejecutar:  python tests/test_server.py
"""
import asyncio
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fake_colab.py"
SECRET = "sk-or-SECRETO-1234567890"

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {detail}"))


def text(res) -> str:
    return "".join(c.text for c in res.content if getattr(c, "type", "") == "text")


def calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


async def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    work = tmp / "work"
    work.mkdir()
    (work / "datos.csv").write_text("a,b\n1,2\n")
    (work / "job.py").write_text("print(1)\n")
    outside = tmp / "fuera.txt"
    outside.write_text("no debe salir")
    (work / "enlace").symlink_to(outside)
    envfile = tmp / "secretos.env"
    envfile.write_text(f"# comentario\nOPENROUTER_API_KEY={SECRET}\nOTRA='valor-con-comillas-99'\n")
    log = tmp / "calls.jsonl"
    fake = tmp / "colab"
    fake.write_text(f"#!{sys.executable}\n" + FAKE.read_text().split("\n", 1)[1])
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    base_env = {
        **os.environ,
        "COLAB_BIN": str(fake),
        "COLAB_MCP_WORKDIR": str(work),
        "COLAB_MCP_ENV_FILE": str(envfile),
        "FAKE_COLAB_LOG": str(log),
        "COLAB_MCP_MAX_OUTPUT": "2000",
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "colab_cli_mcp.server"], env=base_env, cwd=str(ROOT))

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name: t for t in (await s.list_tools()).tools}
            expected = {
                "colab_sessions", "colab_status", "colab_usage", "colab_new", "colab_install", "colab_stop",
                "colab_exec", "colab_run", "colab_upload", "colab_download", "colab_ls", "colab_rm", "colab_log",
            }
            check("lista las 13 herramientas", set(tools) == expected, str(set(tools) ^ expected))
            check("el esquema de colab_exec tiene sus parámetros",
                  {"code", "file", "session", "timeout", "secrets"} <= set(tools["colab_exec"].inputSchema["properties"]))

            async def call(name, **kw):
                return text(await s.call_tool(name, kw))

            # --- ejecución básica y secretos
            out = await call("colab_exec", code="print('x')", session="trainer")
            c = calls(log)[-1]
            check("exec envía el código por stdin", c["stdin"] == "print('x')", str(c))
            check("exec pasa -s y --timeout", c["argv"][:3] == ["exec", "-s", "trainer"] and "--timeout" in c["argv"], str(c["argv"]))
            check("exec devuelve la salida", "hola desde el kernel falso" in out, out)

            out = await call("colab_exec", code="print(1)", secrets=["OPENROUTER_API_KEY"])
            c = calls(log)[-1]
            check("el secreto llega al CLI con --env", f"OPENROUTER_API_KEY={SECRET}" in c["argv"], str(c["argv"]))
            check("el valor del secreto NO vuelve al modelo", SECRET not in out and "***" in out, out)

            out = await call("colab_exec", code="print(1)", secrets=["NO_EXISTE"])
            check("secreto inexistente se rechaza y no ejecuta", out.startswith("ERROR") and "NO_EXISTE" in out and SECRET not in out, out)

            out = await call("colab_exec", code="a", file="job.py")
            check("exec exige code O file", out.startswith("ERROR"), out)
            n = len(calls(log))
            out = await call("colab_exec", file="job.py")
            check("exec con file usa -f con ruta resuelta", calls(log)[-1]["argv"][-2:] == ["-f", str(work / "job.py")], str(calls(log)[-1]))

            # --- confinamiento de rutas locales
            n = len(calls(log))
            for label, kw, tool in [
                ("subir ../ fuera", dict(local_path="../fuera.txt", remote_path="/content/x"), "colab_upload"),
                ("subir ruta absoluta fuera", dict(local_path=str(outside), remote_path="/content/x"), "colab_upload"),
                ("subir vía enlace simbólico fuera", dict(local_path="enlace", remote_path="/content/x"), "colab_upload"),
                ("descargar a ../ fuera", dict(remote_path="/content/a", local_path="../robado.txt"), "colab_download"),
                ("exec -f fuera", dict(file=str(outside)), "colab_exec"),
                ("run script fuera", dict(script="../fuera.txt"), "colab_run"),
            ]:
                out = await call(tool, **kw)
                check(f"rechaza: {label}", out.startswith("ERROR") and ("carpeta de trabajo" in out or "No existe" in out), out)
            check("ninguna ruta rechazada llegó al CLI", len(calls(log)) == n, str(calls(log)[n:]))
            check("nada se escribió fuera de la carpeta", not (tmp / "robado.txt").exists())

            out = await call("colab_upload", local_path="datos.csv", remote_path="/content/datos.csv")
            check("sube un archivo dentro de la carpeta", calls(log)[-1]["argv"] == ["upload", str(work / "datos.csv"), "/content/datos.csv"], str(calls(log)[-1]))
            out = await call("colab_download", remote_path="/content/r.csv", local_path="resultados/r.csv")
            check("descarga y crea subcarpetas", (work / "resultados" / "r.csv").read_text() == "contenido remoto", out)

            # --- validaciones de entrada
            n = len(calls(log))
            bad = [
                ("sesión con espacios/opciones", "colab_status", dict(session="x --auth adc")),
                ("gpu inválida", "colab_new", dict(gpu="RTX9090")),
                ("gpu y tpu", "colab_new", dict(gpu="T4", tpu="v5e1")),
                ("paquete como opción", "colab_install", dict(packages=["--index-url=http://malo"])),
                ("rm raíz", "colab_rm", dict(path="/")),
                ("rm /content", "colab_rm", dict(path="/content/")),
                ("log fuera de rango", "colab_log", dict(lines=0)),
                ("timeout absurdo", "colab_exec", dict(code="1", timeout=99999)),
            ]
            for label, tool, kw in bad:
                out = await call(tool, **kw)
                check(f"valida: {label}", out.startswith("ERROR"), out)
            check("las entradas inválidas no llegaron al CLI", len(calls(log)) == n, str(calls(log)[n:]))

            # --- comandos correctos
            await call("colab_new", session="lab", gpu="t4", high_mem=True)
            check("new arma --gpu T4 --high-mem", calls(log)[-1]["argv"] == ["new", "-s", "lab", "--gpu", "T4", "--high-mem"], str(calls(log)[-1]))
            await call("colab_install", packages=["openai", "pandas>=2.0", "pillow"])
            check("install pasa los paquetes", calls(log)[-1]["argv"] == ["install", "openai", "pandas>=2.0", "pillow"], str(calls(log)[-1]))
            out = await call("colab_log", session="lab", lines=3, event_type="execution")
            check("log pasa --lines y --type", calls(log)[-1]["argv"] == ["log", "-s", "lab", "--lines", "3", "--type", "execution"] and "evento 2" in out, str(calls(log)[-1]))
            await call("colab_stop", session="lab")
            check("stop", calls(log)[-1]["argv"] == ["stop", "-s", "lab"])
            await call("colab_run", script="job.py", args=["--n", "3"], gpu="L4", keep=True)
            check("run arma flags, script y argumentos", calls(log)[-1]["argv"] == ["run", "--gpu", "L4", "--keep", str(work / "job.py"), "--n", "3"], str(calls(log)[-1]))
            await call("colab_rm", path="/content/viejo.csv")
            check("rm de un archivo concreto", calls(log)[-1]["argv"] == ["rm", "/content/viejo.csv"])

            # --- salida y errores
    # recorte de salida y timeout, probados con el módulo directo
    sys.path.insert(0, str(ROOT))
    os.environ.update({k: base_env[k] for k in ("COLAB_BIN", "COLAB_MCP_WORKDIR", "COLAB_MCP_ENV_FILE", "FAKE_COLAB_LOG")})
    from colab_cli_mcp import server

    clipped = server._clip("x" * 100000)
    check("recorta salidas enormes", len(clipped) < server.MAX_OUT + 200 and "recortada" in clipped, str(len(clipped)))
    out = server._run(["exec"], stdin="dormir", timeout=1)  # el CLI falso tarda 5 s
    check("un subproceso que excede el tiempo se informa sin colgarse", out.startswith("ERROR: tiempo agotado"), out)

    os.environ["FAKE_COLAB_MODE"] = "needs_login"
    out = server.colab_sessions()
    check("error de login sugiere iniciar sesión", out.startswith("ERROR") and "iniciar sesión" in out, out)
    os.environ.pop("FAKE_COLAB_MODE")
    server.COLAB_BIN = "/no/existe/colab"
    out = server.colab_sessions()
    check("CLI ausente da instrucción de instalación", "uv tool install google-colab-cli" in out, out)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} pruebas OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
