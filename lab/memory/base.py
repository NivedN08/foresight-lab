"""The lab notebook: what was tried, where the idea came from, and what it measured.

Every backend implements the same two calls,

    memory.add(finding)
    memory.recall(query, as_of=None, k=8, identity="lab") -> RecallResult

and appends every finding it is given to a JSONL file (one Finding per line),
so a run directory always holds the full notebook whatever the backend.
`as_of` hides every finding recorded after that moment; that is what lets a run
be replayed as if it were an earlier date.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from lab.core.timeutil import from_iso, to_iso, utcnow


def _num(x) -> str:
    return "n/a" if x is None else f"{x:+.4g}"


@dataclass
class Finding:
    claim: str                       # one or two sentences: what was tried
    problem: str = ""
    generation: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    time: datetime = field(default_factory=utcnow)
    source_title: str = ""
    source_url: str = ""
    origin: str = "closed_book"      # "search", "closed_book", "single_agent" or "random_mutation"
    known_closed_book: bool | None = None
    accepted: bool = False           # passed the decision rule against the incumbent it was tested on
    select_delta: float | None = None    # candidate minus incumbent, loss units, negative is better
    select_low: float | None = None
    select_high: float | None = None
    confirm_delta: float | None = None
    supersedes: str | None = None
    text: str = ""                   # compact paragraph an agent can read
    became_incumbent: bool | None = None  # adopted at the end of its generation (None: not recorded)
    duplicate_of: str | None = None  # id of a finding in the same generation with the same code or losses
    search_backend: str | None = None     # backend whose hits the claim came from
    source_relevance: str | None = None   # "domain", "method" or "unrelated" (search claims only)
    idea_ref: str | None = None      # researcher mode: the notebook finding id or board idea id it named

    def __post_init__(self):
        if isinstance(self.time, str):
            self.time = from_iso(self.time)
        if self.time.tzinfo is None:
            raise ValueError("Finding.time must be timezone-aware UTC")
        if not self.text:
            self.text = self.render()

    def verdict(self) -> str:
        if not self.accepted:
            return "rejected"
        if self.became_incumbent is True:
            return "ACCEPTED, became incumbent"
        if self.became_incumbent is False:
            return "passed, not adopted"
        return "ACCEPTED"

    def later_window_words(self) -> str:
        """The confirm-window result in words only. Agents never see confirm-window figures:
        they stay in `confirm_delta` for the report and the dashboard."""
        if self.confirm_delta is None:
            return ""
        return "; later-window check: " + ("passed (not worse than the incumbent)" if self.confirm_delta <= 0
                                            else "failed (worse than the incumbent)")

    def render(self) -> str:
        """The text agents read. It may show select-window effects, never confirm-window numbers."""
        parts = [f"[{self.verdict()}] {self.problem} gen {self.generation}: {self.claim.strip()}"]
        if self.select_delta is not None:
            parts.append(f"Select delta {_num(self.select_delta)} "
                         f"(95% interval {_num(self.select_low)} to {_num(self.select_high)}; "
                         f"loss units, negative is better)" + self.later_window_words() + ".")
        else:
            parts.append("Not measured (the candidate did not run).")
        if self.source_title or self.source_url:
            parts.append(f"Source: {self.source_title} {self.source_url}".strip()
                         + (f" ({self.source_relevance})" if self.source_relevance else ""))
        parts.append(f"Origin: {self.origin}"
                     + ("" if self.known_closed_book is None
                        else f"; already known closed-book: {'yes' if self.known_closed_book else 'no'}") + ".")
        return " ".join(parts)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["time"] = to_iso(self.time)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class RecallResult:
    findings: list[Finding]
    text: str            # what would be pasted into a prompt
    latency_s: float
    chars: int


@runtime_checkable
class Memory(Protocol):
    name: str

    def add(self, finding: Finding) -> None: ...

    def recall(self, query: str, *, as_of: datetime | None = None, k: int = 8,
               identity: str = "lab") -> RecallResult: ...


def read_jsonl(path: str | os.PathLike) -> list[Finding]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(Finding.from_dict(json.loads(line)))
    return out


OUTCOME_FIELDS = ("accepted", "became_incumbent", "select_delta", "select_low", "select_high", "confirm_delta")


def shuffle_outcomes(findings: list[Finding], seed: int) -> list[Finding]:
    """The shuffled-notebook control: copies of `findings` whose outcome fields
    (OUTCOME_FIELDS: the verdict and the measured deltas) are permuted across
    findings with `seed`, and whose text is rebuilt from the shuffled fields.
    Claims, sources and ids stay put. With two or more findings the permutation
    is never the identity (a random permutation that happens to be is rotated
    by one), so at least some findings carry another finding's outcome."""
    import random
    from dataclasses import replace

    n = len(findings)
    order = list(range(n))
    random.Random(seed).shuffle(order)
    if n > 1 and order == list(range(n)):
        order = order[1:] + order[:1]
    out = []
    for i, f in enumerate(findings):
        src = findings[order[i]]
        g = replace(f, **{k: getattr(src, k) for k in OUTCOME_FIELDS}, text="")
        g.text = g.render()
        out.append(g)
    return out


def load_findings(source) -> list[Finding]:
    """Findings from a list, a JSONL file, or a run directory (its memory.jsonl if
    present, which includes anything that run itself imported, else findings.jsonl).
    Duplicate ids keep the first copy."""
    if source is None:
        return []
    if isinstance(source, (str, os.PathLike)):
        p = Path(source)
        if p.is_dir():
            p = p / "memory.jsonl" if (p / "memory.jsonl").exists() else p / "findings.jsonl"
        items = read_jsonl(p)
    else:
        items = [f if isinstance(f, Finding) else Finding.from_dict(f) for f in source]
    seen, out = set(), []
    for f in items:
        if f.id not in seen:
            seen.add(f.id)
            out.append(f)
    return out


class LocalMemory:
    """Shared plumbing: a thread-safe in-memory list mirrored to a JSONL file.

    If the file already exists its findings are loaded, so reopening a run
    directory (resume) restores the notebook."""

    name = "local"

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._items: list[Finding] = []
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._items = read_jsonl(self.path)

    def _persist(self, finding: Finding) -> None:
        if self.path is not None:
            with self.path.open("a") as fh:
                fh.write(json.dumps(finding.to_dict()) + "\n")

    def add(self, finding: Finding) -> None:
        with self._lock:
            self._items.append(finding)
            self._persist(finding)

    def all(self) -> list[Finding]:
        with self._lock:
            return list(self._items)

    def visible(self, as_of: datetime | None) -> list[Finding]:
        """Findings recorded at or before `as_of`, minus any that a visible later
        finding supersedes."""
        items = [f for f in self.all() if as_of is None or f.time <= as_of]
        replaced = {f.supersedes for f in items if f.supersedes}
        return [f for f in items if f.id not in replaced]


def render_block(findings: Iterable[Finding]) -> str:
    lines = [f"- {f.text}" for f in findings]
    return "\n".join(lines)
