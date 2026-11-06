"""CLI command groups. Every module here with a `register(main)` function is added to `rankwell`."""

from __future__ import annotations

import importlib
import pkgutil

import click


def register_all(main: click.Group) -> None:
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(module, "register"):
            module.register(main)
