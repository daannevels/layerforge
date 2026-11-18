"""Pure DSP engine: numpy in, numpy out. No file I/O, no printing, no global state.

Every tunable number lives in `constants.py` (global) or `presets.py` (per sound type).
This package is the part that phase 2 ports to C++ (see docs/ENGINE_SPEC.md).
"""
