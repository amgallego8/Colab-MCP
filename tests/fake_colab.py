#!/usr/bin/env python3
"""Ejecutable falso de `colab` para probar el servidor sin cuenta ni red.

Registra cada llamada (argumentos y stdin) en $FAKE_COLAB_LOG como una línea JSON
y responde según el subcomando.
"""
import json
import os
import sys
import time

argv = sys.argv[1:]
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
with open(os.environ["FAKE_COLAB_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": argv, "stdin": stdin}) + "\n")

args = list(argv)
if args[:1] == ["--auth"]:
    args = args[2:]
cmd = args[0] if args else ""

if os.environ.get("FAKE_COLAB_MODE") == "needs_login":
    print("Error: credentials not found, please login", file=sys.stderr)
    sys.exit(1)
if cmd == "exec":
    env = [a for a in args if "=" in a and not a.startswith("-") and args[args.index(a) - 1] == "--env"]
    print("hola desde el kernel falso")
    for e in env:  # simula un programa que imprime su clave por error
        print("leí:", e.split("=", 1)[1])
    if "dormir" in stdin:
        time.sleep(5)
elif cmd == "sessions":
    print("trainer  T4  running")
elif cmd == "new":
    print("sesión creada")
elif cmd == "download":
    open(args[-1], "w").write("contenido remoto")
    print("descargado")
elif cmd == "log":
    print("\n".join(f"evento {i}" for i in range(int(args[args.index("--lines") + 1]))))
elif cmd == "big":
    print("x" * 100000)
else:
    print(f"ok {cmd}")
