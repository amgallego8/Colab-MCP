#!/usr/bin/env python3
"""A fake `colab` executable for tests: no account, no network.

It records every call (argv + stdin) in $FAKE_COLAB_LOG and emulates a VM inside the
folder $FAKE_VM_ROOT: `upload`/`download`/`ls`/`rm` map remote paths into that folder, and
`exec` really runs the code with python (so the snippets that normally run on the VM are
exercised for real). Like the real CLI, a Python exception in `exec` goes to stderr and the
exit code stays 0.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

argv = sys.argv[1:]
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
log = os.environ.get("FAKE_COLAB_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "stdin": stdin}) + "\n")

args = list(argv)
if args[:1] == ["--auth"]:
    args = args[2:]
cmd = args[0] if args else ""
rest = args[1:]
VM = os.environ.get("FAKE_VM_ROOT", "/tmp/fake_vm")
STATE = os.environ.get("FAKE_STATE", os.path.join(VM, ".sessions.json"))


def opt(name, default=None, multi=False):
    vals = [rest[i + 1] for i, a in enumerate(rest) if a == name and i + 1 < len(rest)]
    if multi:
        return vals
    return vals[-1] if vals else default


def positional():
    skip = {"-s", "--session", "--timeout", "--env", "--output-image", "-f", "--file", "--gpu", "--tpu",
            "--lines", "--type", "-n", "-t", "-o", "--output"}
    out, i = [], 0
    while i < len(rest):
        if rest[i] in skip:
            i += 2
            continue
        if rest[i].startswith("-"):
            i += 1
            continue
        out.append(rest[i])
        i += 1
    return out


def vm_path(remote):
    return os.path.join(VM, remote.lstrip("/"))


def sessions():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def save_sessions(d):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(d, open(STATE, "w"))


if os.environ.get("FAKE_COLAB_MODE") == "needs_login":
    print("Error: credentials not found, please login", file=sys.stderr)
    sys.exit(1)

SHIM = '''
import sys, traceback
class _R:
    def __init__(self, ok): self.success = ok
class _IP:
    def run_cell(self, src, **kw):
        try:
            exec(compile(src, "<cell>", "exec"), globals())
            return _R(True)
        except BaseException:
            traceback.print_exc()
            return _R(False)
def get_ipython(): return _IP()
'''

if cmd == "exec":
    if opt("-s") == "ghost":  # emulate a session that does not exist / was lost
        print("[colab] Error: Session 'ghost' not found.")
        sys.exit(1)
    code = stdin
    f = opt("-f") or opt("--file")
    if f:
        code = open(f, encoding="utf-8").read()
    env = dict(os.environ, COLAB_MCP_VM_ROOT=VM)
    for kv in opt("--env", multi=True):
        k, _, v = kv.partition("=")
        env[k] = v
    img = opt("--output-image")
    if img and "FAKE_PLOT" in code:
        open(img, "wb").write(bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6c9bd0000000049454e44ae426082"))
    if "FAKE_SLEEP" in code:
        time.sleep(float(opt("--timeout", "30")) + 5)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as t:
        t.write(SHIM + "\n" + code)
    p = subprocess.run([sys.executable, t.name], capture_output=True, text=True, env=env, cwd=VM)
    os.unlink(t.name)
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    sys.exit(0)
elif cmd == "sessions":
    for name, hw in sessions().items():
        print(f"{name}  {hw}  running")
    if not sessions():
        print("[colab] No active sessions found on server.")
elif cmd == "new":
    s = sessions()
    s[opt("-s", "default")] = opt("--gpu", "CPU")
    save_sessions(s)
    print("[colab] session created")
elif cmd == "status":
    name = opt("-s")
    if name and name not in sessions():
        print(f"[colab] Session '{name}' not found.")
        sys.exit(1)
    print("[colab] status: IDLE")
elif cmd == "stop":
    s = sessions()
    s.pop(opt("-s", "default"), None)
    save_sessions(s)
    print("[colab] stopped")
elif cmd == "upload":
    local, remote = positional()[:2]
    if not os.path.isfile(local):
        print(f"[colab] Local file '{local}' not found.")
        sys.exit(1)
    dest = vm_path(remote)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(local, dest)
    print(f"[colab] Uploaded '{local}' to '{remote}'")
elif cmd == "download":
    remote, local = positional()[:2]
    src = vm_path(remote)
    if not os.path.isfile(src):
        print(f"[colab] Download failed: File or directory not found: {remote}")
        sys.exit(1)
    shutil.copyfile(src, local)
    print(f"[colab] Downloaded '{remote}' to '{local}'")
elif cmd == "ls":
    p = vm_path((positional() or ["content"])[0])
    for n in sorted(os.listdir(p)) if os.path.isdir(p) else []:
        print(n)
elif cmd == "rm":
    os.remove(vm_path(positional()[0]))
    print("[colab] Deleted")
elif cmd == "log":
    n = int(opt("--lines", "5"))
    print("\n".join(f"evento {i}" for i in range(n)))
elif cmd == "run":
    print("[colab] ran " + " ".join(positional()))
else:
    print(f"[colab] ok {cmd}")
