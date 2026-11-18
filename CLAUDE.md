# layerforge — project notes for Claude

Builds NEW drum one-shots by blending the parts the user likes of 2–4 of their own samples of one type
(kick, clap, closedhat, openhat, crash, perc). Phase 1 = Python prototype (engine + local browser UI).
Phase 2 = a JUCE/VST3 port in C++, so the engine must stay portable. The full brief is the user's
"LAYERFORGE - PHASE 1" prompt; this file keeps its rules and the current status.

## The user
- Daan, a music producer, new to coding and GitHub. Does not use a terminal.
- Chat: simple Dutch, short sentences, explain a technical word the first time.
- Code, comments, commit messages, docs: English. Exception: README.md is simple Dutch, written for Daan.
- Testing: after a push to main, Daan downloads the ZIP from GitHub, unzips and double-clicks a launcher.

## Hard rules
1. Work in milestones (below). At the end of each: run all tests, commit, push to main, give a Dutch
   summary (3–6 sentences: what was done, exactly what to click, what to listen/look for), then STOP
   and wait for Daan's OK. Inside a milestone, commit after every working step.
2. Never commit audio (wav, aiff, flac, mp3) or Daan's samples. Tests use synthetic audio made in code
   (`src/layerforge/synth.py`, re-exported by `tests/fixtures.py`). Only exception: `tests/golden/`
   (raw float64 `.f64` vectors, < 2 MB total).
3. Dependencies: Python 3.11+, numpy, scipy, soundfile; pytest (dev); uv runs everything.
   Nothing else without asking. No web framework, no npm, no build step, no CDN: the server is
   `http.server`, the UI is plain HTML/CSS/JS that works offline.
4. Server: binds 127.0.0.1 only; random token in the URL it opens, required on every `/api/` call
   (header `X-Layerforge-Token` or `?token=`); rejects any Host header other than `127.0.0.1:<port>`;
   uploads only wav/aiff/flac up to 50 MB; never builds file paths from client input (static files come
   from a fixed whitelist; workspace files are addressed by server-generated ids).
5. Never claim something works without running it. Report test results in plain words.
6. Ambiguous → choose the simplest option that fits, mention it in the Dutch summary, continue. Ask only
   when a choice cannot be undone or changes the sound concept.
7. Numbers (band edges, times, thresholds) are starting values: keep them in `engine/constants.py`
   (global, each with unit + comment) and `engine/presets.py` (per sound type) so they are easy to tune.
   If a test threshold is physically unreasonable, say so and change it with a comment.

## Commands
- `uv sync` — install
- `uv run pytest` — all tests
- `uv run layerforge ui [--port 8766] [--no-browser]` — local app (opens the browser)
- `uv run layerforge selftest` — quick end-to-end check, prints PASS/FAIL
- `uv run layerforge render <recipe.json>` — rebuild a WAV from its recipe (M3)

## Conventions
- `src/` layout, package `layerforge`, build backend `uv_build`. `uv.lock` is committed.
- `engine/` is PURE: numpy in, numpy out; no file I/O, no printing, no global state; float64 everywhere.
  Frequencies in Hz and times in ms/s at the API; converted to samples inside (any sample rate works;
  test 44.1 and 48 kHz).
- `audio_io.py` is the only module that reads/writes audio files.
- Planning (random, `planner.py`) is separate from rendering (deterministic, `render.py`). The recipe
  (`engine/types.py`, JSON, has `schema_version`) is the contract with phase 2.
- Launchers: `.bat` files are CRLF (enforced by `.gitattributes`); `.command` files carry the executable
  bit in git (`git update-index --chmod=+x`).
- Workspace: `~/Layerforge` (override with `LAYERFORGE_HOME`).
- CI: `.github/workflows/tests.yml` runs pytest + selftest on ubuntu, windows and macos.

## Milestones
- M1 Skeleton (layout, uv, launchers, CI, fixtures, selftest stub, "Layerforge works" page) — done, awaiting OK
- M2 Engine (sections 4–5) + first sound (minimal UI, base variation, compare with simple stack)
- M3 Variations (planner, quality gate, selection, results grid, workspace, `render`)
- M4 Editing and listening tools (spectrogram/waveform editing, solo, mono, loop, A/B, Advanced)
- M5 Polish and port-readiness (README, ENGINE_SPEC, PROJECT_SUMMARY, golden vectors, tag v0.1.0)

## Decisions made so far
- Synthetic generators live in `src/layerforge/synth.py` (not only in tests) so `layerforge selftest`
  works from an installed copy; `tests/fixtures.py` re-exports them.
- Default UI port 8766 (loopforge uses 8765), next free port if taken.
