"""The random-mutation baseline: change a candidate without any model.

One mutation is either
  - "perturb": multiply one to three float constants by exp(N(0, scale)), or
  - "drop_term": replace one `a + b` / `a - b` inside a method body by one of its
    operands (the other term is dropped).
The result must parse, pass the sandbox lint and differ from the input;
otherwise another mutation is drawn (up to `attempts`). Comments are lost
because the source is regenerated from the syntax tree.
"""
from __future__ import annotations

import ast
import math
import random
from dataclasses import dataclass

from lab.sandbox.lint import lint


@dataclass
class Mutation:
    source: str
    kind: str          # "perturb" or "drop_term"
    description: str   # one line, e.g. "line 12: 0.85 -> 0.97"


def _floats(tree: ast.AST) -> list[ast.Constant]:
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, float) and not isinstance(n.value, bool)
            and math.isfinite(n.value) and n.value != 0.0]


def _sums_in_methods(tree: ast.AST) -> list[tuple[ast.AST, str, ast.BinOp]]:
    """(parent, field, binop) for every + / - expression inside a function body."""
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for parent in ast.walk(fn):
            for name, value in ast.iter_fields(parent):
                items = value if isinstance(value, list) else [value]
                for i, child in enumerate(items):
                    if isinstance(child, ast.BinOp) and isinstance(child.op, (ast.Add, ast.Sub)):
                        out.append((parent, f"{name}[{i}]" if isinstance(value, list) else name, child))
    return out


def _set_field(parent: ast.AST, field: str, node: ast.AST) -> None:
    if field.endswith("]"):
        name, idx = field[:-1].split("[")
        getattr(parent, name)[int(idx)] = node
    else:
        setattr(parent, field, node)


def _normal(src: str) -> str:
    try:
        return ast.dump(ast.parse(src))
    except SyntaxError:
        return " ".join(src.split())


def mutate(source: str, rng: random.Random, *, scale: float = 0.3, attempts: int = 30) -> Mutation | None:
    """One lint-clean mutation of `source`, or None if none was found."""
    base = _normal(source)
    for _ in range(attempts):
        tree = ast.parse(source)
        floats = _floats(tree)
        sums = _sums_in_methods(tree)
        kinds = [k for k, ok in (("perturb", floats), ("drop_term", sums)) if ok]
        if not kinds:
            return None
        kind = rng.choice(kinds)
        if kind == "perturb":
            picks = rng.sample(floats, k=min(len(floats), rng.randint(1, 3)))
            notes = []
            for node in picks:
                old = node.value
                new = float(f"{old * math.exp(rng.gauss(0.0, scale)):.6g}")
                node.value = new
                notes.append(f"line {node.lineno}: {old:g} -> {new:g}")
            desc = "perturb constants: " + "; ".join(notes)
        else:
            parent, field, node = rng.choice(sums)
            keep_left = rng.random() < 0.5
            kept = node.left if keep_left else node.right
            if not keep_left and isinstance(node.op, ast.Sub):
                kept = ast.UnaryOp(op=ast.USub(), operand=kept)
            dropped = ast.unparse(node.right if keep_left else node.left)
            _set_field(parent, field, kept)
            desc = f"drop term at line {node.lineno}: removed `{dropped[:80]}` from `{ast.unparse(node)[:120]}`"
        ast.fix_missing_locations(tree)
        try:
            new_src = ast.unparse(tree) + "\n"
        except Exception:
            continue
        if _normal(new_src) == base or lint(new_src):
            continue
        return Mutation(new_src, kind, desc)
    return None
