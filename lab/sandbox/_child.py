"""Child-side runner. NOT imported by the lab: the parent reads this file's
text and runs it with `python -I -S -X utf8 -c <text> <payload> <pkgs> <limits>`.

It must stay self-contained (standard library only, no `lab` imports): the
child's sys.path holds only the standard library and a directory with a link
to numpy.

Protocol: the result is ONE JSON object written to the original stdout file
descriptor. Before the candidate runs, fd 1 and sys.stdout are pointed at
stderr, so anything the candidate prints ends up in the stderr tail and can
never forge the result.
"""
import builtins
import json
import math
import os
import sys
import traceback


def _apply_limits(limits):
    try:
        import resource
    except ImportError:
        return []
    applied = []
    for name, value in limits.items():
        r = getattr(resource, name, None)
        if r is None:
            continue
        try:
            soft, hard = resource.getrlimit(r)
            new_hard = hard if hard != resource.RLIM_INFINITY and hard < value else value
            resource.setrlimit(r, (min(value, new_hard), new_hard))
            applied.append(name)
        except (ValueError, OSError):
            pass
    return applied


class Case:
    """What a candidate sees: plain attributes, no truth."""

    __slots__ = ("case_id", "issue_time", "target_time", "lead_hours", "inputs")

    def __init__(self, case_id, issue_time, target_time, lead_hours, inputs):
        self.case_id = case_id
        self.issue_time = issue_time
        self.target_time = target_time
        self.lead_hours = lead_hours
        self.inputs = inputs

    def __repr__(self):
        return "Case(%r, issue_time=%s, target_time=%s)" % (
            self.case_id, self.issue_time.isoformat(), self.target_time.isoformat())


def _decode(obj, np, dt):
    if isinstance(obj, list):
        return [_decode(v, np, dt) for v in obj]
    if isinstance(obj, dict):
        tag = obj.get("$t")
        if tag is None:
            return {k: _decode(v, np, dt) for k, v in obj.items()}
        v = obj["v"]
        if tag == "dict":
            return {k: _decode(x, np, dt) for k, x in v.items()}
        if tag == "dt":
            return dt.datetime.fromisoformat(v)
        if tag == "date":
            return dt.date.fromisoformat(v)
        if tag == "nd":
            if np is None:
                return v["data"]
            kind = np.dtype(v["dtype"]).kind
            if kind in "mM":
                arr = np.array(v["data"], dtype="int64").view(v["dtype"])
            else:
                arr = np.array(v["data"], dtype=v["dtype"])
            return arr.reshape(v["shape"])
        raise ValueError("unknown tag %r" % tag)
    return obj


def _make_case(d, np, dt):
    return Case(
        d["case_id"],
        dt.datetime.fromisoformat(d["issue_time"]),
        dt.datetime.fromisoformat(d["target_time"]),
        float(d["lead_hours"]),
        _decode(d["inputs"], np, dt),
    )


_BLOCKED_BUILTINS = (
    "open", "eval", "exec", "compile", "input", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "breakpoint", "help", "exit", "quit",
    "memoryview", "copyright", "credits", "license",
)


def _restricted_builtins(allowed_imports):
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0 or name.split(".")[0] not in allowed_imports:
            raise ImportError("import of %r is not allowed in the sandbox" % name)
        return real_import(name, globals, locals, fromlist, level)

    b = {k: v for k, v in vars(builtins).items() if k not in _BLOCKED_BUILTINS}
    b["__import__"] = guarded_import
    return b


def _tail(text, n=15):
    lines = text.rstrip().splitlines()
    return "\n".join(lines[-n:])


class BadPrediction(Exception):
    pass


def _convert_prediction(p, i):
    import numbers
    if not isinstance(p, dict):
        raise BadPrediction("predict() for eval case %d returned %s, expected a dict" % (i, type(p).__name__))
    out = {}
    for k, v in p.items():
        if not isinstance(k, str):
            raise BadPrediction("prediction %d has a non-string key %r" % (i, k))
        if isinstance(v, bool) or not isinstance(v, numbers.Real):
            raise BadPrediction("prediction %d field %r is %s, expected a float" % (i, k, type(v).__name__))
        out[k] = float(v)
    return out


def main():
    payload_path, pkgs_dir, limits_json = sys.argv[1], sys.argv[2], sys.argv[3]
    limits = json.loads(limits_json)
    applied = _apply_limits(limits)

    # keep a private handle on the real stdout; send everything else to stderr
    real_out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def finish(obj):
        obj["rlimits"] = applied
        real_out.write(json.dumps(obj))
        real_out.flush()
        real_out.close()

    stage = "load"
    try:
        if pkgs_dir:
            sys.path.insert(0, pkgs_dir)
        try:
            import numpy as np
        except ImportError:
            np = None
        import datetime as dt

        with open(payload_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        source = payload["source"]
        allowed = frozenset(payload["allowed_imports"])
        train_cases = [_make_case(c, np, dt) for c in payload["train_cases"]]
        train_truths = [_decode(t, np, dt) for t in payload["train_truths"]]
        eval_cases = [_make_case(c, np, dt) for c in payload["eval_cases"]]
        del payload

        ns = {"__builtins__": _restricted_builtins(allowed), "__name__": "candidate"}
        code = compile(source, "<candidate>", "exec")
        exec(code, ns)
        cls = ns.get("Forecaster")
        if not isinstance(cls, type):
            raise TypeError("candidate does not define class Forecaster")
        model = cls()
        name = str(getattr(model, "name", "unnamed"))[:200]

        stage = "fit"
        model.fit(train_cases, train_truths)

        stage = "predict"
        preds = []
        for i, case in enumerate(eval_cases):
            preds.append(_convert_prediction(model.predict(case), i))
    except BaseException as e:  # noqa: BLE001 - report everything, incl. SystemExit
        tb = traceback.format_exc()
        if isinstance(e, MemoryError):
            kind = "memory"
        elif isinstance(e, BadPrediction):
            kind = "bad_output"
        else:
            kind = "crash"
        finish({"ok": False, "stage": stage, "kind": kind,
                "error": "%s: %s" % (type(e).__name__, str(e)[:500]),
                "traceback": _tail(tb)})
        return
    finish({"ok": True, "name": name, "predictions": preds})


main()
