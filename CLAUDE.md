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
- CI: `.github/workflows/tests.yml` runs pytest + selftest on ubuntu and windows (no macOS runner available; Daan tests macOS by hand).

## Milestones
- M1 Skeleton (layout, uv, launchers, CI, fixtures, selftest stub, "Layerforge works" page) — done ✅
- M2 Engine (sections 4–5) + first sound (minimal UI, base variation, compare with simple stack) — done ✅
- M3 Variations (planner, quality gate, selection, results grid, workspace, `render`) + simple
  Low/Mid/High direction UI (Daan's feedback on M2) — done ✅
- M3b (Daan's feedback on M3): instrument profiles, bass + synth types, length/decay matching,
  feedback button (no audio stored), project cleanup — done ✅ (drums "work perfectly" per Daan)
- M3c (Daan's feedback on M3b: bass/synth too close to the originals, tails of one layer ring on):
  tonal blending (shared volume curve + spectral morph) for bass and synth — done, awaiting OK
- M4 Editing and listening tools (spectrogram/waveform editing, solo, mono, loop, A/B, Advanced)
- M5 Polish and port-readiness (README, ENGINE_SPEC, PROJECT_SUMMARY, golden vectors, tag v0.1.0)

## Decisions made so far
- Synthetic generators live in `src/layerforge/synth.py` (not only in tests) so `layerforge selftest`
  works from an installed copy; `tests/fixtures.py` re-exports them.
- Default UI port 8766 (loopforge uses 8765), next free port if taken.
- Zone time 0 (`Recipe.origin`) = the earliest onset after anchor alignment. Anchor alignment delays
  every input to the latest anchor (shifts >= 0), so nothing is cut at the start.
- `planner.plan_base` receives the input audio (it needs band signals for cell energies and
  alignment); `render` uses only numbers from the recipe. Alignment lags/polarities are stored in
  the recipe per band and input.
- Per-band alignment reference = the owner with the most total weight in that band (ties: energy).
- Crossover check: compares the dominant owners of two neighbouring bands; a correction moves the
  whole upper band (all its owners) by the same lag/polarity, processed from low to high.
- Post-chain order: subtract mean → saturation (4x FIR) → 10 Hz HP → start at zero crossing →
  tail trim → subtract the trimmed segment's mean again (decaying kicks carry a small DC) → fades →
  true-peak scale. Saturation runs before the fades so the FIR cannot smear the faded edges.
- Tail threshold is -70 dB relative to the output peak (= dBFS after normalisation).
- Mono-or-stereo: if any input is stereo, all are stereo. Inputs with >2 channels keep the first two.
- UI playback is loudness-matched: every sound is played at -20 dB RMS over its first 200 ms
  (gain capped at +12 dB). Changing the sound type resets the keep-map grids.
- **Simple direction UI (Daan, after M2):** the per-cell keep-map grid is gone from the UI (Daan: do not
  keep it as Advanced; he will ask if he wants it back). The UI sends `groups` = [Low, Mid, High], each an
  input index or null (Free). `presets.group_keepmap` turns that into a keep-map: keep (100 %) on every
  zone of every band in the group; everything else open. `TypePreset.band_groups` maps bands to groups
  (kick: Sub+Body=Low, Mids=Mid, Top+Air=High; other types: Low=Low, Body=Mid, the top two=High).
  The engine still supports the full keep/avoid/strength keep-map.
- Variations: the pool is planned and judged on mono sums of the inputs (half the work for stereo);
  the picked candidates are rendered from the real inputs. Every candidate has its own generator
  `default_rng([seed, candidate])`, and the draw order inside a candidate is fixed.
- Band-edge jitter is applied to a candidate with probability `BAND_EDGE_JITTER_PROB` (0.5); the other
  candidates reuse the cached base band split (speed: 16 variations of four 1 s stereo inputs ≈ 8 s here).
- Extra alignment offsets (jitter) move only the non-lead owners of a band (the lead stays put).
- Quality gate details: silence = RMS of the first 100 ms before normalisation; low-end R is measured on
  the per-input contributions to the output below 200 Hz (FFT brick-wall, measurement only); pool
  candidates are normalised on the sample peak (`fast_peak`), real outputs on the true peak.
- Features: band energies come from the power spectrum with the type's DEFAULT edges.
- Oversampling FIR: Kaiser sinc, 24 taps per side per phase (193 taps at 4x), beta 8, cutoff 0.9 Nyquist.
- `layerforge render vNN.json` finds inputs via `InputSpec.file` (relative to the project folder) and
  writes `vNN_render.wav`; it is byte-identical to `vNN.wav` (tested).
- Uploads are kept in memory by the server; Generate copies them to `Projects/<id>/inputs/inputN.ext`
  (the original names only go into project.json).
- **Instrument profiles (M3b):** one pipeline, per-type rules in `TypePreset` (presets.py):
  `single_owner_below_hz` (decided on DEFAULT edges via `single_owner_bands`, so edge jitter never enables
  low-end mixing — this fixed a bug where kick Body could mix when its edge was jittered above 200 Hz),
  `noise_from_hz` (bands from there up: envelope alignment, no polarity, no crossover check),
  `envelope_align_ms`, `single_attack` (Attack cells never mix; open Attack cells go to one transient
  leader = the High-group source if chosen), `length_group`, `centroid_anchor` (clap), `pitched`
  (bass/synth: pitch shown + warning when inputs differ > 50 cents). `Settings.relaxed_alignment=False`
  turns envelope alignment off (waveform everywhere).
- **Tail continuity:** an open Tail cell follows the band's Sustain owner; open Sustain+Tail are chosen
  together; inputs shorter than `TAIL_OWNER_MIN_RATIO` x target never own open Tail cells.
- **Decay matching:** target decay = decay of the length-group source, else the median (jittered towards
  shortest/longest in variations). Longer inputs get `decay_slope_db_s = DECAY_DB * (1/T - 1/D)` from
  `decay_start` (onset + Attack edge). The fade multiplies every band of the input AFTER the cached split
  (exact: bands still sum to the faded input), stored in `InputSpec`.
- **Parallel pool:** `select.generate` uses spawn worker processes when the pool is big
  (`POOL_PARALLEL_MIN_WORK`), falls back to sequential on any pool failure; results are identical (tested).
- **Feedback:** `server/feedback.py`, one JSON line per remark in `<workspace>/feedback.jsonl`: rating, tags
  (fixed list `TAGS`), text, settings, recipe, measurements of inputs and output. No audio. Export via
  `/api/feedback/export`. Daan sends that file to Claude for tuning.
- **Cleanup:** `workspace.cleanup_projects` keeps the 20 newest folders in Projects/ that match the
  Layerforge name pattern and contain project.json or inputs/ (Daan agreed); runs at start and after
  each Generate. Favourites and feedback.jsonl are never touched.
- **Tonal blending (M3c), `TypePreset.morph` (bass, synth); `engine/morph.py`:** drums are untouched.
  (1) Shared volume curve: per input a dB gain curve on a 10 ms frame grid (`env_gain_db`, from the
  origin) moves its envelope onto one target curve = the length leader's envelope, or (Free) an
  alive-weighted dB average with a raised-cosine release ending at a target length between the inputs
  (geometric mean, jittered). After the release the target is -120 dB (silence). The attack keeps its own
  shape (gain 0 dB during the Attack zone, then a 3-frame ramp, 5-frame smoothing). Decay slopes are 0.
  (2) Spectral morph for `morph_bands` (all bands above the single-owner low end): STFT (sqrt periodic
  Hann, 1024 at 44.1/48 kHz, hop/4, exact OLA), |Y| = power mean (exponent `morph_power`, base 0.5) of
  the inputs' magnitudes with per-bin weights (band weights interpolated in log-frequency, times
  `env_alive`), phase of the low leader below 2x the low crossover, else of the strongest input. The
  morph output is guarded at the origin and split with the same filters; its morph bands are added to the
  low bands from the cells. Group choice -> that input gets weight 0.7 in the group's bands.
  Measured on Daan's TPS Ignite synths/basses (kept only in the scratchpad, never committed): the nearest-
  input spectral distance of the worst variation rose from 0.6 dB (patchwork = almost a copy) to >= 4 dB,
  and lengths now land between the inputs instead of at the longest.
- Recipe schema_version 2 (tonal fields); version-1 recipes still load and render identically.
- Pitch warning compares pitch CLASSES (octaves are fine); Daan's "C" bass pack contains an F1 and a D2.
