"""Markdown blocks that read well in a terminal: tables, sparklines, bars, deltas.

Claude Code renders Markdown, so tools return these ready to show next to the raw numbers.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

SPARKS = "▁▂▃▄▅▆▇█"
GAP = "·"


def sparkline(values: Sequence[float | None], invert: bool = False) -> str:
    """One character per value; None is a gap. invert=True draws low values high (ranks: 1 is best)."""
    nums = [v for v in values if v is not None]
    if not nums:
        return GAP * len(values)
    lo, hi = min(nums), max(nums)
    span = hi - lo or 1
    out = []
    for v in values:
        if v is None:
            out.append(GAP)
            continue
        level = (v - lo) / span
        if invert:
            level = 1 - level
        out.append(SPARKS[round(level * (len(SPARKS) - 1))])
    return "".join(out)


def bar(value: float, maximum: float, width: int = 20) -> str:
    if maximum <= 0 or value <= 0:
        return ""
    filled = value / maximum * width
    full = int(filled)
    partial = "▏▎▍▌▋▊▉"[int((filled - full) * 7)] if filled - full >= 1 / 7 and full < width else ""
    return "█" * full + partial


def delta(change: float | None, good_when_up: bool = True, pct: bool = False) -> str:
    """Signed change with an arrow: ▲ +12 / ▼ -3 / – 0. For ranks pass good_when_up=False."""
    if change is None:
        return "–"
    if change == 0:
        return "– 0"
    up = change > 0
    arrow = "▲" if up else "▼"
    text = f"{change:+.1f}%" if pct else f"{change:+,.0f}" if float(change).is_integer() else f"{change:+,.1f}"
    mark = "" if up == good_when_up else " ⚠"
    return f"{arrow} {text}{mark}"


def rank(r: int | None) -> str:
    return f"#{r}" if r else "–"


def number(n: float | None) -> str:
    if n is None:
        return "–"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 10_000:
        return f"{n / 1000:.0f}k"
    if abs(n) >= 1000:
        return f"{n / 1000:.1f}k"
    return f"{n:,.0f}" if float(n).is_integer() else f"{n:,.2f}"


def _cell(v: Any) -> str:
    if v is None:
        return "–"
    return str(v).replace("|", "\\|").replace("\n", " ")


def table(rows: Sequence[dict[str, Any]], columns: Sequence[str] | None = None,
          headers: dict[str, str] | None = None, align_right: Sequence[str] = ()) -> str:
    """A Markdown table. columns picks and orders keys; headers renames them."""
    if not rows:
        return "_(nothing yet)_"
    cols = list(columns or rows[0].keys())
    names = [(headers or {}).get(c, c) for c in cols]
    sep = ["---:" if c in align_right else "---" for c in cols]
    lines = ["| " + " | ".join(names) + " |", "|" + "|".join(sep) + "|"]
    lines += ["| " + " | ".join(_cell(r.get(c)) for c in cols) + " |" for r in rows]
    return "\n".join(lines)


def bars(items: Sequence[tuple[str, float]], width: int = 24, fmt: str = "{:,.0f}") -> str:
    """Horizontal bar chart in a code block (keeps alignment in any terminal)."""
    if not items:
        return "_(nothing yet)_"
    pad = max(len(k) for k, _ in items)
    top = max(v for _, v in items) or 1
    lines = [f"{k.ljust(pad)}  {bar(v, top, width).ljust(width)}  {fmt.format(v)}" for k, v in items]
    return "```\n" + "\n".join(lines) + "\n```"


def funnel(steps: Sequence[tuple[str, float]], width: int = 24) -> str:
    """Funnel with step-to-step conversion."""
    if not steps:
        return "_(nothing yet)_"
    pad = max(len(k) for k, _ in steps)
    top = max(v for _, v in steps) or 1
    lines = []
    prev = None
    for k, v in steps:
        conv = f"  ({v / prev * 100:.1f}%)" if prev else ""
        lines.append(f"{k.ljust(pad)}  {bar(v, top, width).ljust(width)}  {v:,.0f}{conv}")
        prev = v or None
    return "```\n" + "\n".join(lines) + "\n```"
