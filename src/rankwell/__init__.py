"""Rankwell: App Store Optimization and release assistant for Claude Code."""

from importlib import metadata

try:
    __version__ = metadata.version("rankwell")  # one source of truth: pyproject.toml
except metadata.PackageNotFoundError:
    __version__ = "0.0.0"
