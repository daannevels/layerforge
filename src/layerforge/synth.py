"""Deterministic synthetic drum one-shots for tests and the self-test.

Lives in the package (not only in tests/) so `layerforge selftest` works from an installed copy.
tests/fixtures.py re-exports these. All generators return float64 mono, peak-normalised to 1.0.
"""

from __future__ import annotations

import numpy as np
from scipy import signal


def _normalise(x: np.ndarray) -> np.ndarray:
    return x / np.max(np.abs(x))


def make_kick(f0: float = 170.0, f1: float = 50.0, tau_f: float = 0.03, decay: float = 0.2,
              click: float = 0.4, phase: float = 0.0, seed: int = 1,
              dur: float = 0.6, sr: int = 48000) -> np.ndarray:
    """Sine with a pitch sweep f0 -> f1 (Hz) plus a short high-passed noise click."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    f = f1 + (f0 - f1) * np.exp(-t / tau_f)
    body = np.sin(2 * np.pi * np.cumsum(f) / sr + phase) * np.exp(-t / decay)
    noise = np.random.default_rng(seed).standard_normal(n)
    hp = signal.butter(2, 2500, "hp", fs=sr, output="sos")
    x = body + click * signal.sosfilt(hp, noise) * np.exp(-t / 0.004)
    return _normalise(x)


def make_clap(n_bursts: int = 4, spacing_ms: float = 9.0, decay: float = 0.12,
              centre_hz: float = 1500.0, seed: int = 2, dur: float = 0.5, sr: int = 48000) -> np.ndarray:
    """3-4 band-passed noise bursts a few ms apart, then a longer decaying tail.

    The first burst is the quietest, so the energy centroid of the attack window lies after it.
    """
    n = int(dur * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)
    bp = signal.butter(2, (centre_hz / 2.5, min(centre_hz * 4, 0.45 * sr)), "bandpass", fs=sr, output="sos")
    noise = signal.sosfilt(bp, rng.standard_normal(n))
    env = np.zeros(n)
    for i in range(n_bursts):
        t0 = i * spacing_ms / 1000.0
        amp = 0.5 + 0.5 * i / max(n_bursts - 1, 1)  # bursts grow louder
        tt = t - t0
        burst = np.where(tt >= 0, np.exp(-np.clip(tt, 0, None) / (0.004 if i < n_bursts - 1 else decay)), 0.0)
        env = np.maximum(env, amp * burst)
    return _normalise(noise * env)


def make_hat(decay: float = 0.04, low_hz: float = 6000.0, high_hz: float = 16000.0,
             seed: int = 3, dur: float = 0.3, sr: int = 48000) -> np.ndarray:
    """Band-passed noise with an exponential decay (closed hat; longer decay = open hat)."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    bp = signal.butter(2, (low_hz, min(high_hz, 0.45 * sr)), "bandpass", fs=sr, output="sos")
    noise = signal.sosfilt(bp, np.random.default_rng(seed).standard_normal(n))
    return _normalise(noise * np.exp(-t / decay))


def make_crash(decay: float = 0.6, seed: int = 4, dur: float = 1.5, sr: int = 48000) -> np.ndarray:
    """Wide band-passed noise with a long decay."""
    return make_hat(decay=decay, low_hz=2500.0, high_hz=18000.0, seed=seed, dur=dur, sr=sr)


def make_perc(freq: float = 400.0, decay: float = 0.08, noise_amt: float = 0.3,
              seed: int = 5, dur: float = 0.4, sr: int = 48000) -> np.ndarray:
    """Short damped sine plus a little decaying noise."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    tone = np.sin(2 * np.pi * freq * t) * np.exp(-t / decay)
    noise = np.random.default_rng(seed).standard_normal(n) * np.exp(-t / (decay / 4))
    return _normalise(tone + noise_amt * noise)


def _saw(freq: float, t: np.ndarray, sr: float, phase: float = 0.0) -> np.ndarray:
    """Band-limited sawtooth (additive, harmonics below 0.45 sr)."""
    out = np.zeros_like(t)
    for k in range(1, int(0.45 * sr / freq) + 1):
        out += np.sin(2 * np.pi * k * freq * t + k * phase) / k
    return out


def make_bass(freq: float = 55.0, decay: float = 0.6, drive: float = 2.0, glide: float = 0.15,
              seed: int = 6, dur: float = 1.2, sr: int = 48000) -> np.ndarray:
    """808-style bass note: a sine with a short downward glide into `freq` (Hz), soft-clipped
    (adds harmonics), a 3 ms attack and an exponential decay."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    f = freq * (1.0 + glide * np.exp(-t / 0.02))
    ph = 2 * np.pi * np.cumsum(f) / sr + np.random.default_rng(seed).uniform(0, 2 * np.pi)
    env = np.minimum(t / 0.003, 1.0) * np.exp(-t / decay)
    return _normalise(np.tanh(drive * np.sin(ph)) * env)


def make_synth(freq: float = 220.0, detune: float = 0.006, decay: float = 0.35, seed: int = 7,
               dur: float = 1.0, sr: int = 48000) -> np.ndarray:
    """Synth pluck: three detuned band-limited saws through a decaying low-pass, 5 ms attack."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)
    x = sum(_saw(freq * (1 + d), t, sr, rng.uniform(0, 2 * np.pi)) for d in (-detune, 0.0, detune))
    bright = signal.sosfilt(signal.butter(2, min(6000.0, 0.45 * sr), "lp", fs=sr, output="sos"), x)
    dark = signal.sosfilt(signal.butter(2, 600.0, "lp", fs=sr, output="sos"), x)
    mix = np.exp(-t / 0.08)
    env = np.minimum(t / 0.005, 1.0) * np.exp(-t / decay)
    return _normalise((mix * bright + (1 - mix) * dark) * env)


GENERATORS = {
    "kick": make_kick,
    "clap": make_clap,
    "closedhat": make_hat,
    "openhat": lambda **kw: make_hat(decay=0.25, dur=0.8, **kw),
    "crash": make_crash,
    "perc": make_perc,
    "bass": make_bass,
    "synth": make_synth,
}
