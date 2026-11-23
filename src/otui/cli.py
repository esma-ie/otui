"""otui command line."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from otui import __version__
from otui.bridge import JuliaWorker, setup_env


def _cmd_julia_setup(args) -> int:
    """Set up the pinned TIMES.jl (required) and the latest (default for the server; if it
    fails, otui runs on the pinned one)."""
    want = ["pinned", "latest"] if args.channel == "both" else [args.channel]
    for ch in want:
        try:
            setup_env(channel=ch)
        except Exception as e:  # noqa: BLE001
            if ch == "latest" and "pinned" in want:
                print(
                    f"otui: TIMES.jl's latest version could not be set up ({e}); "
                    "otui runs on the pinned version"
                )
                continue
            raise
    return 0


def _cmd_info(args) -> int:
    from otui.bridge import env_commit

    with JuliaWorker() as w:
        info = w.info()
        info.pop("id", None)
        info.pop("ok", None)
        info["times_jl_channel"] = w.channel
        info["times_jl_commit"] = env_commit(w.channel)
        print(json.dumps({"otui": __version__, **info}, indent=1))
    return 0


def _cmd_demo_dir(args) -> int:
    with JuliaWorker() as w:
        print(w.demo_dir())
    return 0


def _cmd_compile(args) -> int:
    out = Path(args.out or tempfile.mkdtemp(prefix="otui-dump-"))
    with JuliaWorker() as w:
        r = w.compile(args.run, out, dirs=args.dd)
    print(f"{r['symbols']} symbols written to {r['out']} in {r['seconds']:.1f} s")
    return 0


def _cmd_bench(args) -> int:
    """Time to first result: worker start, first compile (cold), second compile (warm)."""
    t0 = time.perf_counter()
    with JuliaWorker() as w:
        started = time.perf_counter() - t0
        run = w.demo_dir() / f"{args.model}.run"
        times = []
        for _ in range(args.repeat):
            with tempfile.TemporaryDirectory(prefix="otui-bench-") as out:
                t = time.perf_counter()
                r = w.compile(run, out)
                times.append(time.perf_counter() - t)
        info = w.info()
    print(f"model           {args.model} ({r['symbols']} symbols)")
    print(f"worker start    {started:6.1f} s  (Julia start and loading TIMES.jl)")
    for i, t in enumerate(times, 1):
        print(f"compile #{i}      {t:6.1f} s  {'(cold)' if i == 1 else '(warm)'}")
    print(f"first result    {started + times[0]:6.1f} s")
    print(f"Julia {info['julia']}, TIMES.jl {info['times_jl']}, threads {info['threads']}")
    return 0


def _cmd_serve(args) -> int:
    import os

    from otui.server import serve

    if args.timesjl:
        os.environ["OTUI_TIMESJL"] = args.timesjl

    serve(port=args.port, warm=not args.no_warm)
    return 0


def _cmd_export(args) -> int:
    from otui import export
    from otui.store import load_dump

    m = load_dump(Path(args.dump))
    out = Path(args.out)
    if out.suffix.lower() == ".xlsx":
        export.write_xlsx(m, out)
    elif out.suffix.lower() == ".zip":
        export.write_csv_zip(m, out)
    else:
        raise ValueError("--out must end in .xlsx or .zip")
    print(f"{len(m.tables)} tables written to {out}")
    return 0


def _cmd_headers(args) -> int:
    """otui table-format headers of every symbol with records in a dump, as a Markdown table."""
    from otui.store import read_manifest, symbols_from_manifest

    syms = [s for s in symbols_from_manifest(read_manifest(Path(args.dump))) if s.has_table]
    print("| Symbol | Kind | Headers | Records | Description |")
    print("| --- | --- | --- | --- | --- |")
    for s in sorted(syms, key=lambda s: s.name):
        value = ["value"] if s.kind == "parameter" else []
        cols = ", ".join(f"`{h}`" for h in [*s.headers, *value]) or "`value`"
        desc = s.description.replace("|", "/")
        print(f"| `{s.name}` | {s.kind} | {cols} | {s.records} | {desc} |")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="otui", description="Open TIMES User Interface")
    p.add_argument("--version", action="version", version=f"otui {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    j = sub.add_parser("julia-setup", help="install otui's Julia environments (TIMES.jl)")
    j.add_argument(
        "--channel",
        choices=["both", "latest", "pinned"],
        default="both",
        help="TIMES.jl's main branch (latest), the commit otui is tested on (pinned), or both",
    )
    j.set_defaults(fn=_cmd_julia_setup)
    sub.add_parser("info", help="show TIMES.jl and Julia versions").set_defaults(fn=_cmd_info)
    sub.add_parser("demo-dir", help="print the folder of the TIMES_Demo models").set_defaults(
        fn=_cmd_demo_dir
    )

    c = sub.add_parser("compile", help="read a .run file and its .dd files with TIMES.jl")
    c.add_argument("run", help="the TIMES run file")
    c.add_argument("--dd", action="append", default=[], help="folder with DD files (repeatable)")
    c.add_argument("--out", help="folder for the Arrow dump (default: a temporary folder)")
    c.set_defaults(fn=_cmd_compile)

    s = sub.add_parser("serve", help="start the local otui server (127.0.0.1)")
    s.add_argument("--port", type=int, default=8642)
    s.add_argument("--no-warm", action="store_true", help="skip the TIMES.jl warm-up")
    s.add_argument(
        "--timesjl",
        choices=["latest", "pinned"],
        help="which TIMES.jl (default: latest if it passes its demo12 check, else pinned)",
    )
    s.set_defaults(fn=_cmd_serve)

    x = sub.add_parser("export", help="write a dump's tables as .xlsx or a CSV .zip")
    x.add_argument("dump", help="folder written by `otui compile`")
    x.add_argument("--out", required=True, help="model.xlsx or model.zip")
    x.set_defaults(fn=_cmd_export)

    h = sub.add_parser("headers", help="table headers of a dump, as Markdown")
    h.add_argument("dump", help="folder written by `otui compile`")
    h.set_defaults(fn=_cmd_headers)

    b = sub.add_parser("bench", help="time to first result on a TIMES_Demo model")
    b.add_argument("--model", default="demo12")
    b.add_argument("--repeat", type=int, default=2)
    b.set_defaults(fn=_cmd_bench)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001 - report any failure as one line
        print(f"otui: {e}", file=sys.stderr)
        return 1
