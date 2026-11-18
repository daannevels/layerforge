"""Command line: `layerforge ui | render <recipe.json> | selftest`."""

from __future__ import annotations

import argparse
import sys

from layerforge import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="layerforge", description="Blend drum one-shots.")
    parser.add_argument("--version", action="version", version=f"layerforge {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ui = sub.add_parser("ui", help="start the local app in your browser")
    ui.add_argument("--port", type=int, default=8766, help="preferred port (next free one is used if taken)")
    ui.add_argument("--no-browser", action="store_true", help="do not open a browser window")

    rnd = sub.add_parser("render", help="rebuild a WAV from a recipe JSON (milestone M3)")
    rnd.add_argument("recipe")

    sub.add_parser("selftest", help="run a quick check and print PASS or FAIL")

    args = parser.parse_args(argv)
    if args.cmd == "ui":
        from layerforge.server.app import serve
        serve(port=args.port, open_browser=not args.no_browser)
        return 0
    if args.cmd == "render":
        print("render is not available yet (milestone M3).", file=sys.stderr)
        return 2
    if args.cmd == "selftest":
        from layerforge.selftest import run
        print(f"Layerforge {__version__} self-test")
        return 0 if run() else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
