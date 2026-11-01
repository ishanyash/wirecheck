"""Command line: ``wirecheck scan`` and ``wirecheck comment``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Sequence

from . import __version__, report, scan


def _load(path: str | None) -> dict[str, Any] | None:
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _exit_code(findings: list[dict[str, Any]], fail_on: str) -> int:
    if fail_on == "never":
        return 0
    if fail_on == "warn":
        return 1 if findings else 0
    return 1 if any(f["severity"] == "error" for f in findings) else 0


def cmd_scan(args: argparse.Namespace) -> int:
    data = scan.scan(args.paths or ["."])
    if args.format == "json":
        text = json.dumps(data, indent=2) + "\n"
    elif args.format == "markdown":
        text = report.render_markdown(None, data)
    else:
        text = scan.render_text(data) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return _exit_code(data["findings"], args.fail_on)


def cmd_comment(args: argparse.Namespace) -> int:
    text = report.render_markdown(_load(args.pytest), _load(args.scan))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wirecheck",
        description="Check code for Claude API version 2026-10-21 problems. "
        "For the runtime check, run: pytest --wirecheck",
    )
    parser.add_argument("--version", action="version", version=f"wirecheck {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="Scan source files for things that break under 2026-10-21.")
    p.add_argument("paths", nargs="*", help="Files or folders (default: .)")
    p.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    p.add_argument("--output", "-o", help="Write to this file instead of stdout.")
    p.add_argument("--fail-on", choices=("error", "warn", "never"), default="error",
                   help="Exit 1 on errors (default), on any finding, or never.")
    p.set_defaults(func=cmd_scan)

    c = sub.add_parser("comment", help="Build the Markdown PR comment from JSON reports.")
    c.add_argument("--pytest", help="JSON from pytest --wirecheck-report")
    c.add_argument("--scan", help="JSON from wirecheck scan --format json")
    c.add_argument("--output", "-o")
    c.set_defaults(func=cmd_comment)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
