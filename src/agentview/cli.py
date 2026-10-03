"""agentview command line interface.

Subcommands:
- `agentview version` prints the installed version
- `agentview report <trace.jsonl> -o report.html` renders a report from a trace
- `agentview demo` runs the product-search demo and writes three reports
- `agentview proxy -- <server command>` wraps an MCP server transparently
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


def _cmd_proxy(server_argv: list[str]) -> int:
    from agentview.proxy import PROXY_EXIT_FAILURE, run_proxy

    if not server_argv:
        print(
            "Usage: agentview proxy -- <server command and args>\n"
            "Example: agentview proxy -- npx -y @modelcontextprotocol/server-filesystem /tmp",
            file=sys.stderr,
        )
        return PROXY_EXIT_FAILURE
    return run_proxy(server_argv)


def main(argv: list[str] | None = None) -> int:
    raw = argv if argv is not None else sys.argv[1:]

    # Handle `agentview proxy -- <server command>` before argparse sees it,
    # because argparse cannot cleanly handle `--` as a separator between
    # subcommand flags and a free-form external command.
    if raw and raw[0] == "proxy":
        try:
            sep = raw.index("--")
            server_argv = raw[sep + 1:]
        except ValueError:
            server_argv = []
        return _cmd_proxy(server_argv)

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

    p = sub.add_parser(
        "proxy",
        help="Wrap an MCP server transparently (use: agentview proxy -- <command>)",
        add_help=False,
    )
    p.set_defaults(func=lambda _: _cmd_proxy([]))

    args = parser.parse_args(raw)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
