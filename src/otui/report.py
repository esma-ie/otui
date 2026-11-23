"""Explaining a run that TIMES.jl cannot do yet, and the report bundle of a run.

``untranslated``: TIMES.jl stops on a TIMES feature it does not translate yet with a message that
names the feature and the GAMS lines (``UnportedError``). otui adds what in the model brings that
feature in (its sets, parameters and run switches named in the message) and where TIMES.jl
tracks its coverage.

``bundle``: "Report this run" in the Run tab. A zip with what a TIMES.jl or otui developer needs
to see and reproduce a run: the versions, each step's time and log, the solver's and TIMES's
logs, the earlier runs of the model, and (if the user includes them) the model's files.
"""

from __future__ import annotations

import io
import json
import platform
import re
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

TIMESJL = "https://github.com/esma-ie/timesjl"
COVERAGE = f"{TIMESJL}/blob/main/docs/gams-julia-map.md"
OTUI_ISSUES = "https://github.com/esma-ie/otui/issues"

UNTRANSLATED = re.compile(r"not yet translated:\s*", re.I)
GAMS_FILE = re.compile(r"\b[\w]+\.(?:mod|vda|dsc|cli|ans|lin|stc|tm|rpt|gms|run|dd)\b", re.I)
SWITCH = re.compile(r"%(\w+)%")
NAME = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
LOG_FILES = ("*.log", "*.lst", "QA_CHECK.LOG", "qa_check.log")
MAX_LOG = 10_000_000  # bytes of one log file in the bundle: its end, if larger
MAX_MODEL = 200_000_000  # bytes of model files in the bundle


def untranslated(message: str, symbols: dict[str, int], switches: dict[str, str]) -> dict:
    """What TIMES.jl does not translate yet, from its message, and what in the model brings it
    in. ``symbols`` maps the model's symbol names to their record counts; ``switches`` are the
    run file's $SET switches. The message may carry a Julia stack trace: its first line with
    "not yet translated" is the feature."""
    lines = message.strip().splitlines() or [""]
    line = next((x for x in lines if UNTRANSLATED.search(x)), lines[0])
    m = UNTRANSLATED.search(line)
    what = (line[m.end() :] if m else line).strip()
    files = list(dict.fromkeys(GAMS_FILE.findall(what)))
    in_model = [
        {"name": n, "records": symbols[n]}
        for n in dict.fromkeys(NAME.findall(what))
        if symbols.get(n)
    ]
    sw = [
        {"name": s, "value": switches.get(s.upper(), "")}
        for s in dict.fromkeys(SWITCH.findall(what))
    ]
    return {
        "what": what,
        "gams_files": files,
        "in_model": in_model,
        "switches": sw,
        "coverage": COVERAGE,
        "issues": f"{TIMESJL}/issues?q="
        + urllib.parse.quote(f"is:issue {files[0] if files else what[:60]}"),
        # a new TIMES.jl issue asking for it, filled in
        "ask": f"{TIMESJL}/issues/new?"
        + urllib.parse.urlencode(
            {
                "title": f"Not yet translated: {what[:120]}",
                "body": f"TIMES.jl stops with:\n\n```\nnot yet translated: {what}\n```\n\n"
                "<!-- Which model and what it uses the feature for. If you can share the "
                "model's DD files, attach them, or otui's report zip of the run. -->\n",
            }
        ),
    }


def is_untranslated(message: str) -> bool:
    return bool(UNTRANSLATED.search(message))


def issue_link(entry: dict[str, Any], env: dict[str, Any]) -> dict[str, str]:
    """Where to report the run, with a title and a short body filled in: TIMES.jl for a run of
    TIMES.jl (its errors and untranslated features), otui otherwise."""
    run = entry.get("run", {})
    res = run.get("result") or {}
    timesjl = run.get("code") == "TIMES.jl"
    model = entry.get("run_file", "")
    if res.get("status") == "not_translated":
        title = f"Not yet translated: {(res.get('untranslated') or {}).get('what', '')[:120]}"
    elif run.get("status") == "error":
        first = (run.get("error") or "").strip().splitlines()[:1]
        title = f"{run.get('solver_label', '')} run fails: {first[0][:120] if first else 'error'}"
    else:
        title = f"{run.get('solver_label', '')} run of {model}: "
    lines = [
        "## What happened",
        "",
        "<!-- What you did, what you expected and what you saw. -->",
        "",
        "## The run",
        f"- Model: {model}",
        f"- Code: {run.get('code', '')} {run.get('version', '')}",
        f"- Solver: {run.get('solver_label', '')}",
        f"- Status: {run.get('status', '')} {res.get('status', '')}".rstrip(),
        f"- Julia {env.get('julia', '')}, {env.get('os', '')}",
        "",
        "The report zip from otui's Run tab is attached (drag it into this box).",
    ]
    base = TIMESJL if timesjl else OTUI_ISSUES.removesuffix("/issues")
    q = urllib.parse.urlencode({"title": title, "body": "\n".join(lines)})
    return {"url": f"{base}/issues/new?{q}", "where": "TIMES.jl" if timesjl else "otui"}


def _tail(p: Path) -> bytes:
    b = p.read_bytes()
    if len(b) <= MAX_LOG:
        return b
    return b"[... the first %d bytes left out ...]\n" % (len(b) - MAX_LOG) + b[-MAX_LOG:]


def markdown(entry: dict[str, Any]) -> str:
    """report.md: the run, its steps and the model's earlier runs, readable on GitHub."""
    env, run = entry["environment"], entry["run"]
    res = run.get("result") or {}
    out = [
        f"# otui run report: {entry.get('run_file', '')}",
        "",
        f"Written {entry['written']} by otui {env.get('otui', '')}.",
        "",
        "## Environment",
        "",
        *(f"- {k}: {v}" for k, v in env.items() if v not in ("", None)),
        "",
        "## The run",
        "",
        f"- Code: {run.get('code', '')} {run.get('version', '')}".rstrip(),
        f"- Solver: {run.get('solver_label', '')}",
        f"- Status: {run.get('status', '')}; result: {res.get('status', '')}",
    ]
    if res.get("objective") is not None:
        out.append(f"- Objective: {res['objective']!r}")
    if res.get("rows"):
        out.append(f"- Model size: {res['rows']} rows x {res.get('columns')} columns")
    if run.get("error"):
        out += ["", "### Error", "", "```", run["error"], "```"]
    u = res.get("untranslated")
    if u:
        out += ["", "### Not translated in TIMES.jl yet", "", u["what"]]
        if u["in_model"]:
            out.append(
                "In the model: "
                + ", ".join(
                    f"{x['name']} ({x['records']} record{'' if x['records'] == 1 else 's'})"
                    for x in u["in_model"]
                )
            )
    out += ["", "## Steps", "", "| step | state | seconds |", "| --- | --- | ---: |"]
    for s in run.get("steps", []):
        secs = "" if s.get("seconds") is None else f"{s['seconds']:.2f}"
        out.append(f"| {s.get('label', s['step'])} | {s.get('state')} | {secs} |")
    for s in run.get("steps", []):
        if s.get("log"):
            out += ["", f"### {s.get('label', s['step'])}", "", "```", *s["log"], "```"]
    if entry.get("parity"):
        out += [
            "",
            "## All runs of this model",
            "",
            "| code | solver | status | objective | rows | seconds |",
            "| --- | --- | --- | ---: | ---: | ---: |",
        ]
        for p in entry["parity"]:
            obj = p.get("objective")
            obj = "" if obj is None else repr(obj)
            out.append(
                f"| {p.get('code', '')} | {p.get('solver_label', '')} | {p.get('status', '')} | "
                f"{obj} | {p.get('rows') or ''} | {p.get('total', 0):.1f} |"
            )
    if entry.get("reference"):
        r = entry["reference"]
        out += [
            "",
            f"Reference objective: {r['model']}, objective {r['objective']!r} ({r['source']}).",
        ]
    files = entry.get("files", [])
    out += ["", "## Model files", ""]
    out += [f"- {f['name']} ({f['bytes']} bytes, sha256 {f['sha256'][:16]})" for f in files]
    out.append("" if entry.get("model_included") else "\nThe model's files are not in this report.")
    return "\n".join(out) + "\n"


README = """\
otui run report
===============

report.md    the run: versions, steps with their times and logs, the model's earlier runs
report.json  the same, for tools
logs/        the solver's log and TIMES's QA_CHECK.LOG (GAMS runs: the listing)
model/       the run file and DD files, if you chose to include them

To report a problem, attach this zip to a new issue:
  TIMES.jl (its errors, features not translated yet, speed): https://github.com/esma-ie/timesjl/issues
  otui (the app): https://github.com/esma-ie/otui/issues

Before you share it: logs can show folder names from your computer, and model/ holds your model's
data. Leave the model out (an option in the Run tab) if it is not yours to share.
"""


def bundle(entry: dict[str, Any], run_dir: Path, files_dir: Path, include_model: bool) -> bytes:
    """The report zip. ``entry`` is the report's data (report.json); ``run_dir`` the last run's
    folder (logs); ``files_dir`` the uploaded model files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", README)
        z.writestr("report.md", markdown(entry))
        z.writestr("report.json", json.dumps(entry, indent=1, default=str))
        if run_dir.is_dir():
            seen: set[Path] = set()
            for pat in LOG_FILES:
                for p in sorted(run_dir.glob(pat)):
                    if p.is_file() and p not in seen:
                        seen.add(p)
                        z.writestr(f"logs/{p.name}", _tail(p))
        if include_model and files_dir.is_dir():
            total = 0
            for p in sorted(files_dir.iterdir()):
                if p.is_file():
                    total += p.stat().st_size
                    if total > MAX_MODEL:
                        z.writestr(
                            "model/TOO_LARGE.txt",
                            "The model's files are over 200 MB; the rest are left out.\n",
                        )
                        break
                    z.write(p, f"model/{p.name}")
    return buf.getvalue()


def environment(health: dict[str, Any]) -> dict[str, Any]:
    """The versions that matter to a run, from /api/health and this computer."""
    runner = health.get("runner") or {}
    return {
        "otui": health.get("otui", ""),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": platform.python_version(),
        "julia": health.get("julia", ""),
        "times_jl": health.get("times_jl", ""),
        "times_jl_channel": runner.get("channel", health.get("times_jl_channel", "")),
        "times_jl_commit": runner.get("commit", health.get("times_jl_commit", "")),
        "times_jl_fallback": runner.get("fallback", ""),
        "times_gams_version_translated": health.get("times_gams_version", ""),
        "threads": health.get("threads", ""),
        "runs_in": (health.get("hosting") or {}).get("kind", ""),  # local or codespaces
    }


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %z")
