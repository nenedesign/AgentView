"""agentview command line interface.

Subcommands:
- `agentview version` prints the installed version
- `agentview report <trace.jsonl> -o report.html` renders a report from a trace
- `agentview demo` runs the product-search demo and writes three reports
- `agentview proxy -- <server command>` wraps an MCP server transparently
- `agentview configure <server-name>` injects the proxy into claude_desktop_config.json
- `agentview restore <server-name>` restores the original entry
- `agentview serve [trace.jsonl]` starts the local dashboard on 127.0.0.1
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
    from agentview.proxy import PROXY_EXIT_FAILURE, MCPInterceptor, run_proxy

    if not server_argv:
        print(
            "Usage: agentview proxy -- <server command and args>\n"
            "Example: agentview proxy -- npx -y @modelcontextprotocol/server-filesystem /tmp",
            file=sys.stderr,
        )
        return PROXY_EXIT_FAILURE

    server_name = Path(server_argv[0]).name
    interceptor = MCPInterceptor(server_name=server_name)
    print(f"agentview proxy: trace -> {interceptor._trace_path}", file=sys.stderr)
    try:
        return run_proxy(server_argv, capture=interceptor.capture)
    finally:
        interceptor.close()
        print(f"agentview proxy: session complete", file=sys.stderr)


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("error: uvicorn is required for 'agentview serve'. Install with: pip install agentview[dashboard]", file=sys.stderr)
        return 2

    from agentview.dashboard.server import create_app, generate_session_token

    host = "127.0.0.1"
    port = args.port

    if getattr(args, "public_bind", False):
        print(
            "\n  WARNING: --public-bind makes the dashboard reachable from other machines.\n"
            "  Only use this in a trusted network with no sensitive trace data.\n",
            file=sys.stderr,
        )
        host = "0.0.0.0"

    token = generate_session_token()
    app = create_app(session_token=token)

    url = f"http://{host}:{port}/?token={token}"
    print(f"agentview dashboard: {url}", file=sys.stderr)
    print(f"agentview dashboard: press Ctrl+C to stop", file=sys.stderr)

    uvicorn.run(app, host=host, port=port, log_level="error")
    return 0


def _cmd_configure(args: argparse.Namespace) -> int:
    from agentview.proxy.config_safety import ConfigError, ConflictError, inject_proxy

    try:
        sha, backup = inject_proxy(
            args.server_name,
            config_path=Path(args.config) if args.config else None,
            dry_run=args.dry_run,
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ConflictError as exc:
        print(f"conflict: {exc}", file=sys.stderr)
        return 3

    if args.dry_run:
        print("(dry run: no changes written)")
    else:
        print(f"Backup written: {backup}")
        print(f"Config updated. Restart Claude Desktop to apply.")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    from agentview.proxy.config_safety import ConfigError, ConflictError, restore_entry

    try:
        backup = restore_entry(
            args.server_name,
            config_path=Path(args.config) if args.config else None,
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ConflictError as exc:
        print(f"conflict: {exc}", file=sys.stderr)
        return 3

    print(f"Backup written: {backup}")
    print(f"Config restored. Restart Claude Desktop to apply.")
    return 0


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

    srv = sub.add_parser("serve", help="Start the local dashboard on 127.0.0.1 (default port 7346)")
    srv.add_argument("--port", type=int, default=7346, help="Port to listen on (default: 7346)")
    srv.add_argument("--public-bind", action="store_true", help="Bind to 0.0.0.0 instead of 127.0.0.1 (WARNING: exposes dashboard on the network)")
    srv.set_defaults(func=_cmd_serve)

    cfg = sub.add_parser("configure", help="Inject agentview proxy into claude_desktop_config.json")
    cfg.add_argument("server_name", help="Name of the MCP server entry to wrap")
    cfg.add_argument("--config", default=None, help="Path to claude_desktop_config.json (default: auto-detect)")
    cfg.add_argument("--dry-run", action="store_true", help="Show the proposed change without writing")
    cfg.set_defaults(func=_cmd_configure)

    rst = sub.add_parser("restore", help="Remove agentview proxy wrapper from an MCP server entry")
    rst.add_argument("server_name", help="Name of the MCP server entry to restore")
    rst.add_argument("--config", default=None, help="Path to claude_desktop_config.json (default: auto-detect)")
    rst.set_defaults(func=_cmd_restore)

    args = parser.parse_args(raw)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
