"""Layerforge: build new drum one-shots by blending parts of 2-4 input samples."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("layerforge")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0+local"
