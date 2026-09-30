"""
Real structural QA checks over a SKOS-style vocabulary dict (as produced by
skos_vocab.concepts_to_dict). These are genuine graph algorithms, not stubs:

  - detect_cycles: DFS-based cycle detection over the broader-> edges.
  - detect_orphans: concepts with no broader/narrower/related edges at all
    (excluding legitimate top concepts, which have narrower or related
    edges but no broader by design).
  - detect_missing_labels: concepts with an empty/whitespace-only prefLabel.

run_qa(concepts) returns a QAReport aggregating all issues found.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class QAReport:
    cycles: List[List[str]] = field(default_factory=list)
    orphans: List[str] = field(default_factory=list)
    missing_labels: List[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not (self.cycles or self.orphans or self.missing_labels)

    def summary(self) -> str:
        lines = []
        lines.append(f"cycles={len(self.cycles)} orphans={len(self.orphans)} "
                      f"missing_labels={len(self.missing_labels)}")
        for c in self.cycles:
            lines.append(f"  CYCLE: {' -> '.join(c)}")
        for o in self.orphans:
            lines.append(f"  ORPHAN: {o}")
        for m in self.missing_labels:
            lines.append(f"  MISSING_LABEL: {m}")
        return "\n".join(lines)


def detect_cycles(concepts: Dict[str, dict]) -> List[List[str]]:
    """DFS cycle detection over the broader-> relation graph."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {cid: WHITE for cid in concepts}
    cycles: List[List[str]] = []
    stack_path: List[str] = []

    def visit(cid: str):
        color[cid] = GRAY
        stack_path.append(cid)
        for b in concepts.get(cid, {}).get("broader", []):
            if b not in concepts:
                continue
            if color[b] == GRAY:
                # found a cycle: extract the cyclic portion of the path
                idx = stack_path.index(b)
                cycles.append(stack_path[idx:] + [b])
            elif color[b] == WHITE:
                visit(b)
        stack_path.pop()
        color[cid] = BLACK

    for cid in concepts:
        if color[cid] == WHITE:
            visit(cid)
    return cycles


def detect_orphans(concepts: Dict[str, dict]) -> List[str]:
    """A concept is an orphan if it has no broader, no narrower, and no
    related links whatsoever (fully disconnected from the taxonomy)."""
    orphans = []
    for cid, c in concepts.items():
        if not c.get("broader") and not c.get("narrower") and not c.get("related"):
            orphans.append(cid)
    return orphans


def detect_missing_labels(concepts: Dict[str, dict]) -> List[str]:
    return [cid for cid, c in concepts.items() if not (c.get("pref_label") or "").strip()]


def run_qa(concepts: Dict[str, dict]) -> QAReport:
    return QAReport(
        cycles=detect_cycles(concepts),
        orphans=detect_orphans(concepts),
        missing_labels=detect_missing_labels(concepts),
    )
