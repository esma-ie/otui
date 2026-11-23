"""QC report: TIMES's own quality-assurance log plus otui's structural checks.

TIMES writes ``QA_CHECK.LOG`` during preprocessing; TIMES.jl writes the same
log (``bridge.JuliaWorker.solve``). Its layout:

    *** <check title>
    *NN <SEVERITY>  - <message>
    *NN <SEVERITY>  - <message>

otui adds checks from the RES network (``graph.build``): dead-end
commodities, unused commodities and disconnected processes.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from otui.graph import Graph

ENTRY = re.compile(r"^\s*\*(\d+)\s+([A-Z][A-Z ]*?)\s+-\s+(.*)$")
TITLE = re.compile(r"^\s*\*\*\*\s+(.*\S)\s*$")
SEVERITY_ORDER = ["FATAL", "SEVERE ERROR", "ERROR", "SEVERE WARNING", "WARNING", "INFO"]

GRAPH_CHECKS = {
    "no_supply": ("ERROR", "Commodity is used but no process produces it"),
    "no_use": ("WARNING", "Commodity is produced but nothing uses it (not a demand or emission)"),
    "unused": ("INFO", "Commodity is declared but no process uses or produces it"),
    "disconnected": ("WARNING", "Process is declared but has no inputs or outputs"),
}


@dataclass
class Issue:
    source: str  # TIMES or otui
    severity: str
    check: str
    message: str
    item: str = ""  # node id for otui checks

    def public(self) -> dict:
        return asdict(self)


def parse_qa_log(text: str) -> list[Issue]:
    issues, title = [], ""
    for line in text.splitlines():
        if m := TITLE.match(line):
            if "QUALITY ASSURANCE LOG" in line or "TIMES -- VERSION" in line:
                continue
            title = m.group(1)
        elif m := ENTRY.match(line):
            issues.append(Issue("TIMES", m.group(2).strip(), title, m.group(3).strip()))
    return issues


def graph_issues(g: Graph) -> list[Issue]:
    out = []
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        for f in sorted(n.flags):
            sev, check = GRAPH_CHECKS[f]
            out.append(Issue("otui", sev, check, n.name, item=n.id))
    return out


def report(g: Graph | None, qa_log: str | None, extra: list[Issue] | None = None) -> dict:
    issues = (graph_issues(g) if g else []) + (parse_qa_log(qa_log) if qa_log else [])
    issues += extra or []

    def rank(i: Issue) -> int:
        return SEVERITY_ORDER.index(i.severity) if i.severity in SEVERITY_ORDER else 99

    issues.sort(key=lambda i: (rank(i), i.source, i.check, i.message))
    counts: dict[str, int] = {}
    for i in issues:
        counts[i.severity] = counts.get(i.severity, 0) + 1
    return {
        "times_log": qa_log is not None,
        "counts": counts,
        "issues": [i.public() for i in issues],
    }
