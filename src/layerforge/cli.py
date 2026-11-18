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

    rnd = sub.add_parser("render", help="rebuild a WAV from a recipe JSON")
    rnd.add_argument("recipe", help="a vNN.json file from a project's variations folder")
    rnd.add_argument("--out", help="output WAV (default: <recipe>_render.wav next to the recipe)")
    rnd.add_argument("--inputs", nargs="+", help="input files, if they are not in the project folder")

    sub.add_parser("selftest", help="run a quick check and print PASS or FAIL")

    args = parser.parse_args(argv)
    if args.cmd == "ui":
        from layerforge.server.app import serve
        serve(port=args.port, open_browser=not args.no_browser)
        return 0
    if args.cmd == "render":
        return _render(args)
    if args.cmd == "selftest":
        from layerforge.selftest import run
        print(f"Layerforge {__version__} self-test")
        return 0 if run() else 1
    return 2


def _render(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from layerforge.audio_io import AudioError, prepare_inputs, read_audio, write_wav
    from layerforge.engine.render import render
    from layerforge.engine.types import Recipe

    path = Path(args.recipe)
    try:
        recipe = Recipe.from_dict(json.loads(path.read_text()))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Cannot read the recipe: {exc}", file=sys.stderr)
        return 2
    project = path.resolve().parent.parent
    if args.inputs:
        files = [Path(f) for f in args.inputs]
    else:
        files = [project / s.file for s in recipe.inputs]
    if len(files) != len(recipe.inputs) or not all(f.is_file() for f in files):
        print("Input files not found; pass them with --inputs (same order as in the recipe).", file=sys.stderr)
        return 2
    try:
        inputs = prepare_inputs([read_audio(f) for f in files], recipe.sample_rate)
    except AudioError as exc:
        print(f"Cannot read an input: {exc}", file=sys.stderr)
        return 2
    if inputs[0].shape[0] != recipe.channels:
        print("The inputs do not match the recipe (mono/stereo).", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else path.with_name(path.stem + "_render.wav")
    write_wav(out, render(recipe, inputs).audio, recipe.sample_rate)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
