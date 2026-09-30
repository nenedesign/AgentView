"""agentview command line interface.

Three subcommands:
- `agentview version` prints the installed version
- `agentview report <trace.jsonl> -o report.html` renders a report from a trace
- `agentview demo` runs the product-search demo and writes three reports
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_version(args: argparse.Namespace) -> int:
    from agentview import __version__

    print(__version__)
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from agentview.report import build_payload, render_to_file

    trace = Path(args.trace)
    if not trace.exists():
        print(f"error: trace file not found: {trace}", file=sys.stderr)
        return 2
    payload = build_payload(trace)
    out = render_to_file(payload, args.output)
    print(f"Wrote {out}")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    from agentview.demo import run_all

    reports = run_all(args.output_dir)
    print("Demo complete. Open any of these in your browser:")
    for r in reports:
        print(f"  {r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentview",
        description="Open agent quality layer: capture agent runs, render a plain-English HTML report.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("version", help="Print the installed version")
    v.set_defaults(func=_cmd_version)

    r = sub.add_parser("report", help="Render a report from a JSONL trace file")
    r.add_argument("trace", help="Path to the JSONL trace file")
    r.add_argument(
        "-o", "--output", required=True, help="Path to write the HTML report to"
    )
    r.set_defaults(func=_cmd_report)

    d = sub.add_parser("demo", help="Run the product-search demo and render three reports")
    d.add_argument(
        "--output-dir",
        default="agentview_demo",
        help="Directory to write traces and reports into (default: agentview_demo)",
    )
    d.set_defaults(func=_cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
