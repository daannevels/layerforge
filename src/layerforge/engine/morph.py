"""Tonal blending for pitched types (bass, synth): shared volume curve + spectral morph.

Two ideas, both deterministic and defined by explicit formulas (docs/ENGINE_SPEC.md):

1. Shared volume curve. Every input gets a slowly varying gain curve so that its loudness over time
   follows one target curve (the length leader's, or an in-between curve). All layers start, swell and
   end together, so no layer keeps ringing on its own. Curves live on a frame grid (ENV_FRAME_MS) in
   the recipe and are linearly interpolated (in dB) per sample.

2. Spectral morph. Above the single-owner low end, the inputs are not cut into band patches but mixed
   in the STFT domain: per bin the magnitude is a weighted power mean of the inputs' magnitudes and the
   phase comes from one input (the low leader near the crossover, the strongest input above). The
   result is one coherent sound whose timbre lies between the inputs.
"""

from __future__ import annotations

import numpy as np

from layerforge.engine import constants as C


# --- shared volume curve -----------------------------------------------------------
def frame_envelope_db(x: np.ndarray, start: int, frame: int, n_frames: int) -> np.ndarray:
    """RMS in dB (relative to the curve's own maximum) of mono x over frames centred at
    start + k * frame, each `frame` samples wide. Floor at ENV_FLOOR_DB."""
    m = x if x.ndim == 1 else x.mean(axis=0)
    out = np.empty(n_frames)
    half = frame // 2
    for k in range(n_frames):
        c = start + k * frame
        seg = m[max(c - half, 0):max(c + half, 0)]
        out[k] = np.sqrt(np.mean(seg * seg)) if seg.size else 0.0
    peak = out.max() if out.size and out.max() > 0 else 1.0
    return np.maximum(20.0 * np.log10(np.maximum(out / peak, 1e-12)), C.ENV_FLOOR_DB)


def curve_length(env_db: np.ndarray, frame: int) -> int:
    """Frames until the curve last reaches LENGTH_DB (its "length")."""
    idx = np.nonzero(env_db >= C.LENGTH_DB)[0]
    return int(idx[-1]) + 1 if idx.size else 1


def target_curve(envs_db: np.ndarray, weights: np.ndarray, length_frames: int) -> np.ndarray:
    """The shared volume curve: a weighted dB average of the envelopes of the inputs that are still
    sounding (each input's weight fades with its own level between ALIVE_LO_DB and ALIVE_HI_DB, so a
    finished input does not drag the curve down), then a raised-cosine release over the last
    RELEASE_FRACTION of `length_frames` down to RELEASE_FLOOR_DB (silence) after it."""
    u = np.clip((envs_db - C.ALIVE_LO_DB) / (C.ALIVE_HI_DB - C.ALIVE_LO_DB), 0.0, 1.0)
    a = weights[:, None] * (u * u * (3.0 - 2.0 * u) + 1e-6)
    t = (a * envs_db).sum(axis=0) / a.sum(axis=0)
    n = t.size
    rel = max(int(round(C.RELEASE_FRACTION * length_frames)), 1)
    k = np.arange(n)
    x = np.clip((k - (length_frames - rel)) / rel, 0.0, 1.0)
    fade_db = 20.0 * np.log10(np.maximum(0.5 + 0.5 * np.cos(np.pi * x), 1e-12))
    return np.maximum(t + fade_db, C.RELEASE_FLOOR_DB)


def gain_curves(envs_db: np.ndarray, target_db: np.ndarray, hold_frames: int) -> np.ndarray:
    """(inputs, frames) gain in dB that moves each envelope onto the target: target - own, clipped to
    [ENV_GAIN_MIN_DB, ENV_GAIN_MAX_DB], 0 dB during the first `hold_frames` (the attack keeps its own
    shape), a linear ramp over ENV_RAMP_FRAMES after that, then smoothed over ENV_SMOOTH_FRAMES."""
    g = np.clip(target_db[None, :] - envs_db, C.ENV_GAIN_MIN_DB, C.ENV_GAIN_MAX_DB)
    n = g.shape[1]
    ramp = np.clip((np.arange(n) - hold_frames) / max(C.ENV_RAMP_FRAMES, 1), 0.0, 1.0)
    g = g * ramp[None, :]
    k = max(C.ENV_SMOOTH_FRAMES, 1)
    if k > 1:
        pad = np.pad(g, ((0, 0), (k // 2, k - 1 - k // 2)), mode="edge")
        g = np.stack([np.convolve(row, np.ones(k) / k, mode="valid") for row in pad])
    return g


def alive_curves(envs_db: np.ndarray, gains_db: np.ndarray) -> np.ndarray:
    """(inputs, frames) 0..1: how much an input still takes part in the morph. Smoothstep of its
    gained level between ALIVE_LO_DB and ALIVE_HI_DB (relative to its own peak)."""
    lvl = envs_db + gains_db
    u = np.clip((lvl - C.ALIVE_LO_DB) / (C.ALIVE_HI_DB - C.ALIVE_LO_DB), 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def curve_to_samples(values: np.ndarray, start: int, frame: int, n: int) -> np.ndarray:
    """Linear interpolation of frame values (frame k at sample start + k * frame) to n samples;
    constant before the first and after the last frame."""
    pos = start + frame * np.arange(len(values), dtype=np.float64)
    return np.interp(np.arange(n, dtype=np.float64), pos, np.asarray(values, dtype=np.float64))


# --- STFT -------------------------------------------------------------------------------
def fft_size_for(sr: float) -> int:
    """Power of two closest to MORPH_FFT_MS at this rate (1024 at 44.1 and 48 kHz)."""
    return int(2 ** round(np.log2(C.MORPH_FFT_MS * sr / 1000.0)))


def _window(n: int) -> np.ndarray:
    """Square root of the periodic Hann window (analysis and synthesis)."""
    return np.sqrt(0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / n))


def stft(x: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """x (n,) -> (frames, n_fft/2+1). x is padded with n_fft zeros on both sides; frame t covers
    padded samples [t*hop, t*hop + n_fft), i.e. it is centred on x index t*hop - n_fft/2."""
    w = _window(n_fft)
    n_frames = int(np.ceil((x.size + n_fft) / hop)) + 1
    xp = np.concatenate([np.zeros(n_fft), x, np.zeros(n_frames * hop + n_fft - x.size)])
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    return np.fft.rfft(xp[idx] * w[None, :], axis=1)


def frame_centres(n_frames: int, n_fft: int, hop: int) -> np.ndarray:
    """x index of the centre of each STFT frame."""
    return hop * np.arange(n_frames) - n_fft // 2


def istft(spec: np.ndarray, n_fft: int, hop: int, n: int) -> np.ndarray:
    """Overlap-add inverse of `stft`. With sqrt-Hann windows and hop = n_fft/4 the window products
    sum to 2 everywhere, so the result is divided by n_fft / (2 * hop)."""
    w = _window(n_fft)
    frames = np.fft.irfft(spec, n_fft, axis=1) * w[None, :]
    out = np.zeros(spec.shape[0] * hop + n_fft)
    for t in range(spec.shape[0]):
        out[t * hop:t * hop + n_fft] += frames[t]
    return out[n_fft:n_fft + n] / (n_fft / (2.0 * hop))


def bin_weights(band_weights: np.ndarray, edges_hz: list[float], freqs: np.ndarray) -> np.ndarray:
    """(inputs, bins): band weights spread over frequency, linear in log-frequency between band
    centres (geometric mean of a band's edges; the outer bands use edge/2 and edge*2)."""
    e = [float(v) for v in edges_hz]
    centres = [e[0] / 2.0] + [np.sqrt(a * b) for a, b in zip(e[:-1], e[1:])] + [e[-1] * 2.0]
    lc = np.log(np.asarray(centres))
    lf = np.log(np.maximum(freqs, 1.0))
    return np.stack([np.interp(lf, lc, band_weights[:, i]) for i in range(band_weights.shape[1])])


def morph(xs: np.ndarray, band_w: np.ndarray, edges_hz: list[float], sr: float, alive: np.ndarray,
          alive_start: int, alive_frame: int, power: float, leader: int, leader_below_hz: float
          ) -> np.ndarray:
    """xs: (inputs, n) aligned, gained mono-or-one-channel signals. band_w: (bands, inputs).
    Returns the morphed signal (n,).

    Per STFT frame t and bin f:  W_i = w_i(f) * alive_i(t), normalised over i (w alone if all dead);
    |Y| = (sum_i W_i |X_i|^p)^(1/p);  phase(Y) = phase of the leader below leader_below_hz, else of
    the input with the largest W_i |X_i|."""
    n_in, n = xs.shape
    n_fft = fft_size_for(sr)
    hop = n_fft // C.MORPH_HOP_DIV
    specs = np.stack([stft(x, n_fft, hop) for x in xs])            # (inputs, frames, bins)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    wf = bin_weights(band_w, edges_hz, freqs)                        # (inputs, bins)
    t_samples = frame_centres(specs.shape[1], n_fft, hop)
    al = np.stack([np.interp(t_samples, alive_start + alive_frame * np.arange(alive.shape[1]), alive[i])
                   for i in range(n_in)])                            # (inputs, frames)
    W = wf[:, None, :] * al[:, :, None]                              # (inputs, frames, bins)
    tot = W.sum(axis=0, keepdims=True)
    W = np.where(tot > 1e-9, W / np.maximum(tot, 1e-12), wf[:, None, :] / np.maximum(wf.sum(axis=0), 1e-12)[None, None, :])
    mag = np.abs(specs)
    p = float(np.clip(power, 0.05, 2.0))
    out_mag = np.sum(W * mag ** p, axis=0) ** (1.0 / p)
    strongest = np.argmax(W * mag, axis=0)                           # (frames, bins)
    phase_src = np.where((freqs < leader_below_hz)[None, :], leader, strongest)
    ph = np.take_along_axis(specs, phase_src[None, :, :], axis=0)[0]
    unit = np.where(np.abs(ph) > 0, ph / np.maximum(np.abs(ph), 1e-300), 1.0)
    return istft(out_mag * unit, n_fft, hop, n)
