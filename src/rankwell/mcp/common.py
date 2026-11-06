"""Shared helpers for MCP tools: one database, errors as readable answers."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, ParamSpec

from .. import db as dbmod
from ..errors import RankwellError

P = ParamSpec("P")


def db() -> dbmod.Database:
    return dbmod.get()


def safe(fn: Callable[P, dict[str, Any]]) -> Callable[P, dict[str, Any]]:
    """Turn expected errors into {"error": ...} so Claude can tell the user what to do."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except RankwellError as e:
            return {"error": str(e)}

    return wrapper
