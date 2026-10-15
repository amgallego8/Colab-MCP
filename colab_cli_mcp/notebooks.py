"""Local `.ipynb` engine: create, read, edit, run cell by cell, convert.

Notebooks are plain files inside COLAB_MCP_WORKDIR. Editing never needs a Colab session;
only `run_cell` / `run_all` talk to a runtime (through `colab exec`, where the kernel
and its variables persist between calls).
"""

import base64
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook, new_output

from .core import ToolError, clip, format_result, local_path, run_colab_raw, session_args, strip_ansi, workdir

SENTINEL = "@@COLAB_MCP_CELL@@"
CELL_TYPES = {"code", "markdown"}
TRASH_DIR = ".colab-mcp-trash"
IMAGE_DIR = ".colab-mcp/images"


# ------------------------------------------------------------------ load / save


def _nb_path(path: str, *, must_exist: bool) -> Path:
    p = local_path(path)
    if p.suffix != ".ipynb":
        raise ToolError("Notebook paths must end in .ipynb.")
    if must_exist and not p.is_file():
        raise ToolError(f"Notebook not found: {path}")
    return p


def _load(path: str):
    p = _nb_path(path, must_exist=True)
    try:
        nb = nbformat.read(str(p), as_version=4)
    except Exception as exc:  # malformed JSON, unsupported format...
        raise ToolError(f"Could not read notebook: {exc}") from None
    for cell in nb.cells:
        if not cell.get("id"):
            cell["id"] = new_code_cell()["id"]
    return p, nb


def _save(p: Path, nb) -> None:
    nbformat.validate(nb)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    os.close(fd)
    try:
        nbformat.write(nb, tmp)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _index_of(nb, cell: str) -> int:
    for i, c in enumerate(nb.cells):
        if c.get("id") == cell:
            return i
    if cell.lstrip("-").isdigit():
        i = int(cell)
        if -len(nb.cells) <= i < len(nb.cells):
            return i % len(nb.cells)
    raise ToolError(f"No cell with id or index {cell!r}. Use colab_notebook_read to see the ids.")


def _make_cell(spec: dict):
    ctype = spec.get("type", "code")
    if ctype not in CELL_TYPES:
        raise ToolError("Cell type must be 'code' or 'markdown'.")
    source = spec.get("source", "")
    if not isinstance(source, str):
        raise ToolError("Cell source must be text.")
    return new_markdown_cell(source) if ctype == "markdown" else new_code_cell(source)


# ------------------------------------------------------------------ rendering


def _summarize_output(out) -> str:
    otype = out.get("output_type")
    if otype == "stream":
        return f"[{out.get('name', 'stdout')}] {out.get('text', '')}".rstrip()
    if otype == "error":
        return f"[error] {out.get('ename', 'Error')}: {out.get('evalue', '')}"
    data = out.get("data", {})
    if "text/plain" in data:
        extra = [m for m in data if m != "text/plain"]
        suffix = f"  (+ {', '.join(extra)})" if extra else ""
        return f"[result] {data['text/plain']}{suffix}"
    return f"[display] {', '.join(data) or 'empty'}"


def _render_cell(i: int, cell, include_outputs: bool) -> str:
    head = f"--- cell {i} | id={cell['id']} | {cell['cell_type']}"
    if cell["cell_type"] == "code":
        head += f" | run={cell.get('execution_count')}"
    parts = [head + " ---", cell["source"] or "(empty)"]
    if include_outputs and cell["cell_type"] == "code" and cell.get("outputs"):
        parts.append("  >> outputs:")
        parts += ["  " + strip_ansi(_summarize_output(o)).replace("\n", "\n  ") for o in cell["outputs"]]
    return "\n".join(parts)


# ------------------------------------------------------------------ create / list / read


def create(path: str, cells: list[dict] | None, overwrite: bool) -> str:
    p = _nb_path(path, must_exist=False)
    if p.exists() and not overwrite:
        raise ToolError(f"{path} already exists. Pass overwrite=true to replace it.")
    nb = new_notebook()
    nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
    for spec in cells or []:
        nb.cells.append(_make_cell(spec))
    _save(p, nb)
    return f"Created {p.relative_to(workdir())} with {len(nb.cells)} cell(s)."


def list_notebooks(directory: str = ".") -> str:
    base = local_path(directory)
    if not base.is_dir():
        raise ToolError(f"Not a directory: {directory}")
    rows = []
    for p in sorted(base.rglob("*.ipynb")):
        rel = p.relative_to(workdir())
        if ".ipynb_checkpoints" in p.parts or TRASH_DIR in rel.parts or p.is_symlink():
            continue
        try:
            n = len(nbformat.read(str(p), as_version=4).cells)
        except Exception:
            n = -1
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))
        rows.append(f"{rel}  | cells: {n if n >= 0 else 'unreadable'} | modified: {when}")
    return "\n".join(rows) or "No notebooks found."


def read(path: str, include_outputs: bool = True, start: int = 0, end: int | None = None) -> str:
    _, nb = _load(path)
    cells = list(enumerate(nb.cells))[start:end]
    body = "\n\n".join(_render_cell(i, c, include_outputs) for i, c in cells)
    return clip(f"{path}: {len(nb.cells)} cell(s)\n\n{body}" if body else f"{path}: no cells in that range.")


# ------------------------------------------------------------------ edit


def add_cell(path: str, source: str, cell_type: str, position: int | None, after: str | None) -> str:
    p, nb = _load(path)
    if position is not None and after is not None:
        raise ToolError("Give `position` or `after`, not both.")
    cell = _make_cell({"type": cell_type, "source": source})
    if after is not None:
        idx = _index_of(nb, after) + 1
    elif position is None:
        idx = len(nb.cells)
    else:
        if not 0 <= position <= len(nb.cells):
            raise ToolError(f"position must be between 0 and {len(nb.cells)}.")
        idx = position
    nb.cells.insert(idx, cell)
    _save(p, nb)
    return f"Added {cell_type} cell at index {idx} (id={cell['id']})."


def edit_cell(path: str, cell: str, source: str, cell_type: str | None) -> str:
    p, nb = _load(path)
    i = _index_of(nb, cell)
    target = nb.cells[i]
    if cell_type and cell_type != target["cell_type"]:
        new = _make_cell({"type": cell_type, "source": source})
        new["id"] = target["id"]
        nb.cells[i] = new
    else:
        target["source"] = source
        if target["cell_type"] == "code":
            target["outputs"] = []  # stale outputs no longer match the source
            target["execution_count"] = None
    _save(p, nb)
    return f"Edited cell {i} (id={nb.cells[i]['id']})."


def delete_cell(path: str, cell: str) -> str:
    p, nb = _load(path)
    i = _index_of(nb, cell)
    removed = nb.cells.pop(i)
    _save(p, nb)
    return f"Deleted cell {i} (id={removed['id']}); {len(nb.cells)} cell(s) left."


def move_cell(path: str, cell: str, to_index: int) -> str:
    p, nb = _load(path)
    i = _index_of(nb, cell)
    if not 0 <= to_index < len(nb.cells):
        raise ToolError(f"to_index must be between 0 and {len(nb.cells) - 1}.")
    nb.cells.insert(to_index, nb.cells.pop(i))
    _save(p, nb)
    return f"Moved cell from {i} to {to_index}."


def clear_outputs(path: str, cell: str | None) -> str:
    p, nb = _load(path)
    targets = [nb.cells[_index_of(nb, cell)]] if cell else nb.cells
    n = 0
    for c in targets:
        if c["cell_type"] == "code" and (c.get("outputs") or c.get("execution_count")):
            c["outputs"], c["execution_count"] = [], None
            n += 1
    _save(p, nb)
    return f"Cleared outputs of {n} cell(s)."


def trash(path: str) -> str:
    """Move a notebook to <workdir>/.colab-mcp-trash/ (recoverable). Never hard-deletes."""
    p = _nb_path(path, must_exist=True)
    dest_dir = workdir() / TRASH_DIR
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{p.name}"
    shutil.move(str(p), str(dest))
    return f"Moved {path} to {dest.relative_to(workdir())} (recoverable)."


# ------------------------------------------------------------------ run


def _wrap_cell(source: str) -> str:
    """Run a cell through IPython so we learn success/failure without parsing tracebacks."""
    return (
        f"_cm_r = get_ipython().run_cell({source!r})\n"
        f"print({SENTINEL!r} + ('ok' if _cm_r.success else 'error'))\n"
    )


def _split_status(stdout: str) -> tuple[str, str | None]:
    status = None
    kept = []
    for line in stdout.splitlines():
        if line.startswith(SENTINEL):
            status = line[len(SENTINEL):].strip()
        else:
            kept.append(line)
    return "\n".join(kept), status


def _record_outputs(cell, nb, text: str, errtext: str, ok: bool, image: Path | None) -> None:
    outs = []
    if text.strip():
        outs.append(new_output("stream", name="stdout", text=text + ("\n" if not text.endswith("\n") else "")))
    if errtext.strip():
        if ok:
            outs.append(new_output("stream", name="stderr", text=errtext + "\n"))
        else:
            first = errtext.strip().splitlines()[-1] if errtext.strip() else "Error"
            ename, _, evalue = first.partition(":")
            outs.append(new_output("error", ename=ename.strip() or "Error", evalue=evalue.strip(), traceback=errtext.splitlines()))
    if image and image.is_file():
        outs.append(new_output("display_data", data={"image/png": base64.b64encode(image.read_bytes()).decode()}, metadata={}))
    cell["outputs"] = outs
    counts = [c.get("execution_count") or 0 for c in nb.cells if c["cell_type"] == "code"]
    cell["execution_count"] = max(counts, default=0) + 1


def run_cell(path: str, cell: str, session: str | None, timeout: float) -> tuple[str, bool]:
    """Run one code cell on the runtime and store its outputs in the notebook. Returns (text, ok)."""
    if not 1 <= timeout <= 7200:
        raise ToolError("timeout must be between 1 and 7200 seconds.")
    p, nb = _load(path)
    i = _index_of(nb, cell)
    target = nb.cells[i]
    if target["cell_type"] != "code":
        raise ToolError(f"Cell {i} is {target['cell_type']}, not code.")
    if not target["source"].strip():
        return f"Cell {i} is empty; nothing to run.", True

    image = workdir() / IMAGE_DIR / f"{p.stem}-{target['id']}.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.unlink(missing_ok=True)
    args = ["exec", *session_args(session), "--timeout", str(timeout), "--output-image", str(image)]
    res = run_colab_raw(args, stdin=_wrap_cell(target["source"]), timeout=timeout + 60)

    if res.problem or (res.returncode != 0):
        # The CLI itself failed (no session, auth, timeout): do not touch the notebook.
        return format_result(res), False

    text, status = _split_status(res.stdout)
    ok = status != "error"
    _record_outputs(target, nb, text, res.stderr, ok, image if image.is_file() else None)
    _save(p, nb)

    label = {"ok": "ok", "error": "ERROR"}.get(status or "", "unknown (status line missing)")
    lines = [f"[cell {i} id={target['id']} status={label}]", clip(text.strip()), clip(res.stderr.strip())]
    if image.is_file():
        lines.append(f"[plot saved: {image.relative_to(workdir())}]")
    return "\n".join(l for l in lines if l), ok


def run_all(path: str, session: str | None, timeout_per_cell: float, stop_on_error: bool, start: int) -> str:
    _, nb = _load(path)
    report = []
    ran = failed = 0
    for i, c in enumerate(nb.cells):
        if i < start or c["cell_type"] != "code" or not c["source"].strip():
            continue
        text, ok = run_cell(path, str(i), session, timeout_per_cell)
        ran += 1
        report.append(text)
        if not ok:
            failed += 1
            if stop_on_error:
                report.append(f"Stopped at cell {i} (stop_on_error=true).")
                break
    summary = f"Ran {ran} cell(s); {failed} failed."
    return clip(summary + "\n\n" + "\n\n".join(report))


# ------------------------------------------------------------------ convert


def to_script(path: str, out_path: str | None) -> str:
    _, nb = _load(path)
    chunks = []
    for c in nb.cells:
        if c["cell_type"] == "markdown":
            chunks.append("\n".join("# " + line for line in c["source"].splitlines()))
        elif c["cell_type"] == "code":
            lines = [
                ("# " + line) if line.lstrip().startswith(("!", "%")) else line
                for line in c["source"].splitlines()
            ]
            chunks.append("\n".join(lines))
    script = "\n\n".join(chunks) + "\n"
    if out_path is None:
        return clip(script)
    dest = local_path(out_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(script, encoding="utf-8")
    return f"Wrote {dest.relative_to(workdir())} (lines starting with ! or % were commented out)."
