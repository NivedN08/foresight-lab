"""Static checks on candidate source, run before anything is executed.

The lint is the first of several layers (see docs/sandbox.md). It is meant to
reject the obvious ways out of the sandbox with a clear message, not to be a
complete Python jail; the child process limits and, on macOS, sandbox-exec
are what actually stop a candidate that gets past it.
"""
from __future__ import annotations

import ast

ALLOWED_IMPORTS = frozenset({
    "math", "numpy", "statistics", "dataclasses", "typing",
    "datetime", "collections", "itertools", "functools",
})

BANNED_CALLS = frozenset({
    "open", "eval", "exec", "compile", "__import__", "input", "globals",
    "locals", "vars", "getattr", "setattr", "delattr", "breakpoint",
})

# numpy file / network / foreign-code helpers. Matched as attribute names on
# any object (so `m = np; m.load(...)` is caught too) and as names imported
# from numpy. A user method called e.g. `save` is also flagged; rename it.
BANNED_NUMPY_ATTRS = frozenset({
    "load", "save", "savez", "savez_compressed", "fromfile", "tofile",
    "memmap", "open_memmap", "genfromtxt", "loadtxt", "savetxt", "fromregex",
    "DataSource", "ctypeslib", "npyio", "dump", "f2py", "testing",
    "distutils",
})

# Attribute names that lead from an allowed module to a dangerous one
# (for example typing.sys, dataclasses.inspect, numpy.f2py.os), and frame /
# code-object attributes that allow walking out of the candidate's scope.
BANNED_ATTRS = frozenset({
    "os", "sys", "subprocess", "socket", "builtins", "importlib", "ctypes",
    "io", "shutil", "pathlib", "posix", "nt", "mmap", "pickle", "marshal",
    "tempfile", "urllib", "http", "threading", "signal", "gc", "inspect",
    "runpy", "multiprocessing", "platform", "sysconfig", "types",
    "extbuild",
    "gi_frame", "gi_code", "cr_frame", "cr_code", "ag_frame", "ag_code",
    "f_globals", "f_locals", "f_builtins", "f_back", "f_code", "tb_frame",
    "tb_next", "co_code", "get_type_hints", "ForwardRef",
}) | BANNED_NUMPY_ATTRS

MAX_LINES = 400
MAX_CHARS = 30_000
MAX_LITERAL_ELEMENTS = 200
MAX_LITERAL_CHARS = 2_000

_DISPLAY = (ast.List, ast.Tuple, ast.Set, ast.Dict)


def _dunder(name: str | None) -> bool:
    return bool(name) and name.startswith("__") and name != "__init__"


def _literal_size(node: ast.AST) -> tuple[int, int]:
    """(element count, total string/bytes chars) for a literal display.

    Elements are summed over the display and every display nested in it
    (a dict entry counts once), so a 20 x 20 nested list counts as 420."""
    elements = 0
    chars = 0
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Dict):
            elements += len(n.keys)
            kids = [k for k in n.keys if k is not None] + list(n.values)
        else:
            elements += len(n.elts)
            kids = list(n.elts)
        for k in kids:
            if isinstance(k, _DISPLAY):
                stack.append(k)
            elif isinstance(k, ast.Constant) and isinstance(k.value, (str, bytes)):
                chars += len(k.value)
    return elements, chars


def lint(source: str, *, max_lines: int = MAX_LINES, max_chars: int = MAX_CHARS) -> list[str]:
    """Return a list of violations ("line N: ..."); empty means clean."""
    problems: list[str] = []
    if not isinstance(source, str):
        return ["line 0: source must be a string"]
    n_lines = source.count("\n") + (0 if source.endswith("\n") or not source else 1)
    if n_lines > max_lines:
        problems.append(f"line {max_lines + 1}: source has {n_lines} lines; the limit is {max_lines}")
    if len(source) > max_chars:
        problems.append(f"line 0: source has {len(source)} characters; the limit is {max_chars}")
    if problems:
        return problems
    try:
        tree = ast.parse(source, filename="<candidate>")
    except SyntaxError as e:
        return [f"line {e.lineno or 0}: syntax error: {e.msg}"]

    def add(node: ast.AST, msg: str) -> None:
        problems.append(f"line {getattr(node, 'lineno', 0)}: {msg}")

    # literals: only check outermost displays (nested ones are counted inside)
    inner: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, _DISPLAY):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, _DISPLAY):
                    inner.add(id(child))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    add(node, f"import of '{a.name}' is not allowed (allowed: {', '.join(sorted(ALLOWED_IMPORTS))})")
                if a.asname and _dunder(a.asname):
                    add(node, f"name '{a.asname}' starts with a double underscore")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                add(node, "relative imports are not allowed")
            elif mod.split(".")[0] not in ALLOWED_IMPORTS:
                add(node, f"import from '{mod}' is not allowed (allowed: {', '.join(sorted(ALLOWED_IMPORTS))})")
            for a in node.names:
                if a.name == "*":
                    add(node, f"'from {mod} import *' is not allowed")
                elif a.name in BANNED_ATTRS or a.name in BANNED_CALLS:
                    add(node, f"importing '{a.name}' from '{mod}' is not allowed (file, network or introspection helper)")
                if _dunder(a.name) or _dunder(a.asname):
                    add(node, f"name '{a.asname or a.name}' starts with a double underscore")
        elif isinstance(node, ast.Attribute):
            if _dunder(node.attr):
                add(node, f"attribute '{node.attr}' starts with a double underscore")
            elif node.attr in BANNED_NUMPY_ATTRS:
                add(node, f"attribute '.{node.attr}' is a numpy file/network helper and is not allowed")
            elif node.attr in BANNED_ATTRS:
                add(node, f"attribute '.{node.attr}' is not allowed (reaches a module or frame outside the allowed set)")
        elif isinstance(node, ast.Name):
            if node.id in BANNED_CALLS:
                add(node, f"use of '{node.id}' is not allowed")
            elif _dunder(node.id):
                add(node, f"name '{node.id}' starts with a double underscore")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if _dunder(node.name):
                add(node, f"definition '{node.name}' starts with a double underscore")
        elif isinstance(node, ast.arg):
            if _dunder(node.arg):
                add(node, f"argument '{node.arg}' starts with a double underscore")
        elif isinstance(node, ast.keyword):
            if _dunder(node.arg):
                add(node, f"keyword '{node.arg}' starts with a double underscore")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                if _dunder(name):
                    add(node, f"name '{name}' starts with a double underscore")

        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            if len(node.value) > MAX_LITERAL_CHARS:
                add(node, f"string literal of {len(node.value)} characters; the limit is {MAX_LITERAL_CHARS}")
        if isinstance(node, _DISPLAY) and id(node) not in inner:
            n_el, n_ch = _literal_size(node)
            kind = type(node).__name__.lower()
            if n_el > MAX_LITERAL_ELEMENTS:
                add(node, f"{kind} literal with {n_el} elements; the limit is {MAX_LITERAL_ELEMENTS} (no lookup tables)")
            elif n_ch > MAX_LITERAL_CHARS:
                add(node, f"{kind} literal holding {n_ch} characters of strings; the limit is {MAX_LITERAL_CHARS}")

    # the contract: a top-level class Forecaster with fit and predict
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Forecaster"), None)
    if cls is None:
        problems.append("line 0: no top-level 'class Forecaster' found")
    else:
        methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
        for m in ("fit", "predict"):
            if m not in methods:
                problems.append(f"line {cls.lineno}: class Forecaster has no '{m}' method")

    # stable order: by line number, then text
    def key(p: str) -> tuple[int, str]:
        try:
            return int(p.split(":", 1)[0].split()[1]), p
        except (IndexError, ValueError):
            return 0, p

    return sorted(dict.fromkeys(problems), key=key)
