"""All global engine constants in one place. Each has a unit in its comment.

These are starting values (literature + reasoning), meant to be tuned by ear.
Per-sound-type values (band edges, zone edges, default drive) live in presets.py.
"""

# --- Workflow limits -------------------------------------------------------
MIN_INPUTS = 2                 # count: fewest input samples per blend
MAX_INPUTS = 4                 # count: most input samples per blend
MIN_VARIATIONS = 1             # count
MAX_VARIATIONS = 16            # count
DEFAULT_VARIATIONS = 8         # count
DEFAULT_VARIATION_AMOUNT = 50  # percent (0..100)
OUTPUT_RATES = (44100, 48000)  # Hz: supported output sample rates

# --- Step 1: analysis --------------------------------------------------------
ONSET_THRESHOLD = 0.10         # fraction of peak |x|: first sample at or above this is the rough onset
ONSET_QUIET = 0.02             # fraction of peak |x|: walk back to the last sample below this
ONSET_MAX_WALKBACK_MS = 5.0    # ms: walk back at most this far
DECAY_DB = -30.0               # dB re envelope peak: decay time is measured down to this level
ENVELOPE_MS = 5.0              # ms: RMS window for the decay envelope
ATTACK_ENVELOPE_MS = 1.0       # ms: RMS window for the attack-time envelope
ATTACK_LOW = 0.10              # fraction of envelope peak: attack time starts here
ATTACK_HIGH = 0.90             # fraction of envelope peak: attack time ends here

# --- Step 2: level match --------------------------------------------------------
LEVEL_MATCH_LIMIT_DB = 12.0    # dB: level-match gain is clamped to +- this
TRIM_LIMIT_DB = 12.0           # dB: per-sample trim range is +- this

# --- Step 4: band split ---------------------------------------------------------
FILTER_ORDER_DEFAULT = 2       # Butterworth order of each LP/HP section (Advanced: 1, 2 or 3)
FILTER_ORDERS = (1, 2, 3)      # allowed filter orders
FILTER_PAD_S = 0.3             # s: zeros added on both sides before forward-backward filtering

# --- Step 5: onset guard --------------------------------------------------------
GUARD_MS_DEFAULT = 1.0         # ms: raised-cosine fade-in length that ends at the onset

# --- Step 6: time-zone windows -------------------------------------------------
CROSSFADE_PERIODS = 0.5        # periods of the band's lowest frequency used as crossfade width
CROSSFADE_MIN_FREQ_HZ = 30.0   # Hz: f_low used for the lowest band (whose lower edge is 0)
CROSSFADE_MIN_MS = 1.0         # ms: shortest zone crossfade
CROSSFADE_MAX_MS = 20.0        # ms: longest zone crossfade

# --- Keep-map ------------------------------------------------------------------
KEEP_STRENGTH_MIN = 0.25       # fraction: weakest keep strength (25 %)
KEEP_STRENGTH_DEFAULT = 1.0    # fraction: default keep strength (100 %)

# --- Step 7: alignment ----------------------------------------------------------
ALIGN_MAX_LAG_MS = 1.5         # ms: lag search range is +- this
ALIGN_WINDOW_MS = 150.0        # ms: energy of the sum is measured over this window after the zone origin
ALIGN_MIN_GAIN_DB = 0.5        # dB: an alignment is applied only if it raises the sum energy this much
CROSSOVER_R_MIN = 0.5          # ratio: crossover check acts when E(a+b)/(E(a)+E(b)) is below this
CROSSOVER_OCTAVES = 1.0        # octaves: width of the band-pass around a shared band edge
ENVELOPE_WINDOW_MS = 1.0       # ms: RMS window of the envelopes used to align noise-like bands
ENVELOPE_ALIGN_MIN_GAIN = 0.02  # fraction: an envelope lag is applied only if it raises the envelope
                                # correlation by at least this much over lag 0

# --- Length (decay matching) -------------------------------------------------------
DECAY_MATCH = True             # every input longer than the target length is made to die away as fast
                               # as the target (an extra exponential fade after the Attack zone)
TAIL_OWNER_MIN_RATIO = 0.7     # ratio: inputs whose decay is below this x target never own open Tail cells
JITTER_LENGTH = 0.5            # without a length leader, the target decay moves exp(U(-1,1) * this * j *
                               # ln(longest / shortest)) around the median, clamped to [shortest, longest]

# --- Pitch (pitched types only, information for the user) -----------------------------
F0_MIN_HZ = 25.0               # Hz: lowest detectable pitch
F0_MAX_HZ = 2000.0             # Hz: highest detectable pitch
F0_SEGMENT_MS = 250.0          # ms: analysed segment, starting F0_SKIP_MS after the onset
F0_SKIP_MS = 20.0              # ms
F0_MIN_CLARITY = 0.5           # normalised autocorrelation needed to report a pitch
PITCH_WARN_CENTS = 50.0        # cents: inputs further apart than this get a warning

# --- Section 6: variations ------------------------------------------------------
POOL_FACTOR = 6                # candidates per requested variation (pool size P = 6 N)
JITTER_STRENGTH = 0.25         # fraction: keep strengths x (1 + U(-1,1) * this * j)
JITTER_BAND_EDGE = 0.15        # band edges x exp(U(-1,1) * this * j)
BAND_EDGE_JITTER_PROB = 0.5    # probability: a candidate moves its band edges (else it reuses the cached base split)
BAND_EDGE_MIN_RATIO = 1.5      # ratio: neighbouring band edges stay at least this far apart
BAND_EDGE_MAX_FRACTION = 0.45  # fraction of the sample rate: highest allowed band edge
JITTER_ZONE_EDGE = 0.20        # zone edges x exp(U(-1,1) * this * j)
JITTER_ALIGN_MS = 0.5          # ms: extra per-band alignment offsets are U(-1,1) * this * j
JITTER_TRIM_DB = 1.5           # dB: per-band trims are U(-1,1) * this * j
JITTER_DRIVE_DB = 3.0          # dB: saturation drive + U(0,1) * this * j
GATE_SILENCE_DBFS = -60.0      # dBFS: RMS of the first GATE_SILENCE_MS (before normalisation) must exceed this
GATE_SILENCE_MS = 100.0        # ms
GATE_LOW_END_HZ = 200.0        # Hz: low-end coherence is measured below this
GATE_LOW_END_R_MIN = 0.5       # ratio: drop candidates whose low-end R is below this
GATE_PEAK_OVER_BASE_DB = 12.0  # dB: drop candidates whose pre-normalisation peak exceeds the base by more
FEATURE_WINDOW_MS = 150.0      # ms: spectral centroid window after the start
POOL_MAX_WORKERS = 6           # count: most worker processes for the candidate pool
POOL_PARALLEL_MIN_WORK = 4_000_000  # candidate-samples: use worker processes above this much work
DUPLICATE_TOL = 1e-9           # feature distance below which two candidates count as identical

# --- Step 9: stereo -------------------------------------------------------------
MONO_BELOW_HZ = 120.0          # Hz: the Side channel is high-passed here (output is mono below)
MONO_FILTER_ORDER = 2          # Butterworth order of the Side high-pass (run forward-backward)

# --- Step 10: post chain ------------------------------------------------------
POST_HP_HZ = 10.0              # Hz: zero-phase DC/subsonic high-pass
POST_HP_ORDER = 2              # Butterworth order of that high-pass
START_PREROLL_MS = 1.0         # ms: the file starts at the zero crossing nearest to onset minus this
START_SEARCH_MS = 1.0          # ms: zero crossings are searched within +- this of that point
FADE_IN_MAX_MS = 1.0           # ms: longest raised-cosine fade-in at the file start
TAIL_THRESHOLD_DB = -70.0      # dB re output peak (= dBFS after normalisation): tail silence level
TAIL_HOLD_MS = 50.0            # ms: the envelope must stay below the threshold this long to trim
FADE_OUT_MS = 10.0             # ms: raised-cosine fade-out at the end
FADE_OUT_MIN_MS = 5.0          # ms: the fade-out is never shorter than this
DRIVE_MAX_DB = 6.0             # dB: largest saturation drive
TRUE_PEAK_DBFS = -0.3          # dBFS: target true peak of the output

# --- Oversampling FIR (saturation and true-peak) -------------------------------
OVERSAMPLE = 4                 # factor
FIR_HALF_TAPS = 24             # input-rate samples on each side of the kernel centre (193 taps at 4x)
FIR_KAISER_BETA = 8.0          # Kaiser window parameter (about 80 dB stop-band)
FIR_CUTOFF = 0.90              # fraction of the input-rate Nyquist frequency

# --- Output ---------------------------------------------------------------------
OUTPUT_SUBTYPE = "PCM_24"      # WAV sample format
RECIPE_SCHEMA_VERSION = 1      # bump when the recipe format changes
