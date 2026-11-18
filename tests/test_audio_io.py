import io

import numpy as np
import pytest
import soundfile as sf

from layerforge.audio_io import AudioError, default_output_rate, prepare_inputs, read_audio, wav_bytes
from tests.fixtures import make_kick


def _bytes(x, sr, fmt="WAV", subtype="PCM_24"):
    buf = io.BytesIO()
    sf.write(buf, x, sr, format=fmt, subtype=subtype)
    return buf.getvalue()


@pytest.mark.parametrize("fmt,subtype", [("WAV", "PCM_24"), ("WAV", "FLOAT"), ("AIFF", "PCM_16"), ("FLAC", "PCM_24")])
def test_read_formats(fmt, subtype):
    x = make_kick(sr=44100) * 0.5
    data, sr = read_audio(_bytes(x, 44100, fmt, subtype))
    assert sr == 44100 and data.shape == (1, x.size)
    assert np.max(np.abs(data[0] - x)) < 1e-4


def test_reject_bad_files():
    with pytest.raises(AudioError):
        read_audio(b"not audio at all")
    with pytest.raises(AudioError):
        read_audio(_bytes(np.zeros(1000), 48000))


def test_prepare_inputs_rate_and_channels():
    mono = (make_kick(sr=44100)[None], 44100)
    stereo = (np.stack([make_kick(sr=48000)] * 2), 48000)
    out = prepare_inputs([mono, stereo], 48000)
    assert all(x.shape[0] == 2 for x in out)
    assert abs(out[0].shape[1] - int(make_kick(sr=44100).size * 48000 / 44100)) <= 1
    out = prepare_inputs([mono, mono], 44100)
    assert all(x.shape[0] == 1 for x in out)


def test_default_rate():
    assert default_output_rate([44100, 44100, 48000]) == 44100
    assert default_output_rate([96000]) == 48000
    assert default_output_rate([44100, 48000]) == 48000


def test_wav_roundtrip_24bit():
    x = make_kick()[None] * 0.9
    data, sr = read_audio(wav_bytes(x, 48000))
    assert sr == 48000 and np.max(np.abs(data - x)) < 2 ** -22
    info = sf.info(io.BytesIO(wav_bytes(x, 48000)))
    assert info.subtype == "PCM_24"
