"""Python snippets that run ON the Colab VM (through `colab exec`) and report back as JSON.

Every user-controlled string is embedded with repr(), never by string concatenation into code,
so quotes, backslashes and newlines in arguments cannot change what the snippet does.

For tests, COLAB_MCP_VM_ROOT (read on the VM side) maps absolute VM paths into a local folder;
on a real VM it is unset and paths are used as they are.
"""

import json

from .core import Result, ToolError, run_colab_raw, session_args

JSON_TAG = "@@COLAB_MCP_JSON@@"

PRELUDE = r'''
import json, os, sys
_VM = os.environ.get("COLAB_MCP_VM_ROOT", "/")
def _p(path):
    return os.path.normpath(os.path.join(_VM, str(path).lstrip("/")))
def _emit(obj):
    print("@@COLAB_MCP_JSON@@" + json.dumps(obj))
JOB_ROOT = _p(os.environ.get("COLAB_MCP_JOB_ROOT", "/content/.colab_mcp") + "/jobs")
TMP_ROOT = _p("/content/.colab_mcp/tmp")
'''


def run_snippet(session: str | None, body: str, args: dict | None = None, timeout: float = 120.0):
    """Run PRELUDE + `ARGS = {...}` + body on the VM. Returns (parsed_json | None, Result)."""
    code = PRELUDE + f"\nARGS = json.loads({json.dumps(args or {})!r})\n" + body
    res = run_colab_raw(["exec", *session_args(session), "--timeout", str(timeout)], stdin=code, timeout=timeout + 60)
    payload = None
    for line in res.stdout.splitlines():
        if line.startswith(JSON_TAG):
            try:
                payload = json.loads(line[len(JSON_TAG):])
            except json.JSONDecodeError:
                payload = None
    return payload, res


def need(payload, res: Result, what: str) -> dict:
    if payload is None:
        from .core import format_result

        raise ToolError(f"{what} failed.\n{format_result(res)}")
    if isinstance(payload, dict) and payload.get("error"):
        raise ToolError(f"{what} failed: {payload['error']}")
    return payload


# --------------------------------------------------------------------------- jobs

JOB_START = r'''
import shlex, subprocess, time, uuid
os.makedirs(JOB_ROOT, exist_ok=True)
jid = "%d-%s" % (int(time.time()), uuid.uuid4().hex[:6])
log, exitf, meta = (os.path.join(JOB_ROOT, jid + s) for s in (".log", ".exit", ".json"))
wrapped = "( %s ) > %s 2>&1; echo $? > %s" % (ARGS["command"], shlex.quote(log), shlex.quote(exitf))
cwd = _p("/content") if os.path.isdir(_p("/content")) else os.getcwd()
proc = subprocess.Popen(["bash", "-c", wrapped], cwd=cwd, start_new_session=True,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
json.dump({"id": jid, "name": ARGS.get("name") or "", "command": ARGS["command"], "pid": proc.pid,
           "started": time.time()}, open(meta, "w"))
_emit({"id": jid, "pid": proc.pid})
'''

JOB_COMMON = r'''
import time
def _load(jid):
    path = os.path.join(JOB_ROOT, jid + ".json")
    if not os.path.isfile(path):
        return None
    return json.load(open(path))
def _state(m):
    exitf = os.path.join(JOB_ROOT, m["id"] + ".exit")
    if os.path.isfile(exitf):
        raw = open(exitf).read().strip()
        if raw == "cancelled":
            return "cancelled", None
        return ("finished", int(raw)) if raw.lstrip("-").isdigit() else ("finished", None)
    try:
        os.kill(m["pid"], 0)
        return "running", None
    except ProcessLookupError:
        return "lost", None
    except PermissionError:
        return "running", None
def _describe(m):
    st, code = _state(m)
    return {"id": m["id"], "name": m.get("name", ""), "command": m["command"], "state": st,
            "exit_code": code, "seconds": round(time.time() - m["started"])}
'''

JOB_STATUS = JOB_COMMON + r'''
m = _load(ARGS["id"])
_emit(_describe(m) if m else {"error": "no such job"})
'''

JOB_LOGS = JOB_COMMON + r'''
m = _load(ARGS["id"])
if not m:
    _emit({"error": "no such job"})
else:
    path = os.path.join(JOB_ROOT, m["id"] + ".log")
    lines = open(path, errors="replace").read().splitlines() if os.path.isfile(path) else []
    d = _describe(m)
    d["log"] = "\n".join(lines[-int(ARGS["tail"]):])
    d["total_lines"] = len(lines)
    _emit(d)
'''

JOB_CANCEL = JOB_COMMON + r'''
import signal
m = _load(ARGS["id"])
if not m:
    _emit({"error": "no such job"})
else:
    st, _ = _state(m)
    if st == "running":
        try:
            os.killpg(os.getpgid(m["pid"]), signal.SIGTERM)
        except ProcessLookupError:
            pass
        open(os.path.join(JOB_ROOT, m["id"] + ".exit"), "w").write("cancelled")
    _emit(_describe(m))
'''

JOB_LIST = JOB_COMMON + r'''
out = []
if os.path.isdir(JOB_ROOT):
    for f in sorted(os.listdir(JOB_ROOT)):
        if f.endswith(".json"):
            out.append(_describe(json.load(open(os.path.join(JOB_ROOT, f)))))
_emit({"jobs": out})
'''

# --------------------------------------------------------------------------- directory transfer

UNZIP = r'''
import zipfile, shutil
dest = _p(ARGS["dest"])
os.makedirs(dest, exist_ok=True)
base = os.path.realpath(dest)
n = 0
with zipfile.ZipFile(_p(ARGS["zip"])) as z:
    for info in z.infolist():
        target = os.path.realpath(os.path.join(dest, info.filename))
        if target != base and not target.startswith(base + os.sep):
            _emit({"error": "unsafe path in archive: " + info.filename}); raise SystemExit(0)
    z.extractall(dest)
    n = len(z.infolist())
os.remove(_p(ARGS["zip"]))
_emit({"files": n, "dest": ARGS["dest"]})
'''

ZIP_DIR = r'''
import zipfile
src = _p(ARGS["src"])
if not os.path.isdir(src):
    _emit({"error": "not a directory: " + ARGS["src"]}); raise SystemExit(0)
os.makedirs(TMP_ROOT, exist_ok=True)
out = os.path.join(TMP_ROOT, ARGS["name"] + ".zip")
total = n = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".ipynb_checkpoints")]
        for f in files:
            full = os.path.join(root, f)
            if os.path.islink(full):
                continue
            z.write(full, os.path.relpath(full, src)); n += 1; total += os.path.getsize(full)
_emit({"zip": "/content/.colab_mcp/tmp/" + ARGS["name"] + ".zip", "files": n, "bytes": total, "zip_bytes": os.path.getsize(out)})
'''

REMOVE_FILE = r'''
try:
    os.remove(_p(ARGS["path"]))
except FileNotFoundError:
    pass
_emit({"ok": True})
'''

# --------------------------------------------------------------------------- runtime info

INFO = r'''
import platform, shutil, subprocess
info = {"python": platform.python_version(), "platform": platform.platform(), "cpus": os.cpu_count()}
try:
    for line in open("/proc/meminfo"):
        if line.startswith("MemTotal"):
            info["ram_gb"] = round(int(line.split()[1]) / 1024 / 1024, 1)
        if line.startswith("MemAvailable"):
            info["ram_available_gb"] = round(int(line.split()[1]) / 1024 / 1024, 1)
except OSError:
    pass
du = shutil.disk_usage(_p("/content")) if os.path.isdir(_p("/content")) else shutil.disk_usage("/")
info["disk_free_gb"] = round(du.free / 1e9, 1)
if shutil.which("nvidia-smi"):
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=20).stdout.strip()
        info["gpu"] = out or None
    except Exception as e:
        info["gpu"] = "nvidia-smi failed: %s" % e
else:
    info["gpu"] = None
_emit(info)
'''


# --------------------------------------------------------------------------- Google Drive on the VM
# Mounting is interactive (the user approves in a browser), so it is never done from here.

DRIVE_STATUS = r'''
import shutil
my = os.path.join(_p("/content/drive"), "MyDrive")
out = {"mounted": os.path.isdir(my)}
if out["mounted"]:
    du = shutil.disk_usage(my)
    out["free_gb"], out["total_gb"] = round(du.free / 1e9, 1), round(du.total / 1e9, 1)
    out["projects_dir_exists"] = os.path.isdir(os.path.join(my, ARGS["projects"]))
_emit(out)
'''

DRIVE_PROJECT = r'''
my = os.path.join(_p("/content/drive"), "MyDrive")
if not os.path.isdir(my):
    _emit({"error": "Google Drive is not mounted on this session."})
else:
    os.makedirs(os.path.join(my, ARGS["projects"], ARGS["name"]), exist_ok=True)
    _emit({"path": "/content/drive/MyDrive/%s/%s" % (ARGS["projects"], ARGS["name"])})
'''
