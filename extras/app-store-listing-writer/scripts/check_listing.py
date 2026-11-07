#!/usr/bin/env python3
"""Check App Store listing text against Apple's limits and common keyword waste.

Standard library only, no network. Usage:
    python3 check_listing.py listing.json
    python3 check_listing.py --name "..." --subtitle "..." --keywords "a,b,c" [--track "habit tracker,streaks"]
listing.json holds any of: name, subtitle, keywords, promotional_text, description, whats_new, and optionally
"track": ["terms", "you want to rank for"]. Exit code 1 when there is a problem that must be fixed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

LIMITS = {"name": 30, "subtitle": 30, "keywords": 100, "promotional_text": 170, "description": 4000,
          "whats_new": 4000}
INDEXED = ("name", "subtitle", "keywords")  # the only text fields Apple uses for search
FILLER = {"app", "apps"}
STOPWORDS = {"a", "an", "the", "and", "or", "for", "of", "to", "in", "on", "with", "by", "your", "my", "&"}


def words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def plural_pairs(ws: set[str]) -> list[tuple[str, str]]:
    out = []
    for w in sorted(ws):
        for p in (w + "s", w + "es", w[:-1] + "ies" if w.endswith("y") else ""):
            if p and p != w and p in ws:
                out.append((w, p))
    return out


def check(fields: dict[str, str], track: list[str]) -> tuple[list[str], list[str], list[str]]:
    lengths, problems, tips = [], [], []
    for k, v in fields.items():
        if k in LIMITS:
            lengths.append(f"{k:<17}{len(v):>5} / {LIMITS[k]:<5}{LIMITS[k] - len(v):>5} left")
            if len(v) > LIMITS[k]:
                problems.append(f"{k}: {len(v)} characters; the limit is {LIMITS[k]}.")
        if v != v.strip():
            problems.append(f"{k}: leading or trailing spaces.")
    if "name" in fields and len(fields["name"].strip()) < 2:
        problems.append("name: needs at least 2 characters.")

    kw = fields.get("keywords")
    kw_words: list[str] = []
    if kw is not None:
        spaces = len(re.findall(r"\s+,|,\s+", kw))
        if spaces:
            problems.append(f"keywords: spaces around commas waste characters ({spaces}x); write 'a,b,c'.")
        terms = [t.strip().lower() for t in kw.split(",")]
        if any(not t for t in terms):
            problems.append("keywords: empty entry (double or trailing comma).")
        kw_words = [w for t in terms for w in words(t)]
        dupes = sorted({w for w in kw_words if kw_words.count(w) > 1})
        if dupes:
            problems.append(f"keywords: repeated words: {', '.join(dupes)}. Each word counts once.")
        filler = sorted(set(kw_words) & FILLER)
        if filler:
            tips.append(f"keywords: '{', '.join(filler)}' is wasted: every result is an app.")

    where: dict[str, list[str]] = {}
    for f in INDEXED:
        if fields.get(f):
            for w in dict.fromkeys(kw_words if f == "keywords" else words(fields[f])):
                if w not in STOPWORDS:
                    where.setdefault(w, []).append(f)
    for w, fs in where.items():
        if len(fs) > 1:
            save = f" Remove it from keywords to save {len(w) + 1} characters." if "keywords" in fs else ""
            tips.append(f"'{w}' is in {' and '.join(fs)}; Apple combines these fields, so once is enough.{save}")
    for single, plural in plural_pairs(set(where)):
        tips.append(f"both '{single}' and '{plural}': one form is usually enough.")
    missing = [t for t in dict.fromkeys(x.strip().lower() for x in track if x.strip())
               if not all(w in where or w in STOPWORDS for w in words(t))]
    if missing:
        tips.append("target terms not covered by name, subtitle or keywords: " + ", ".join(missing))
    return lengths, problems, tips


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="listing.json")
    for f in LIMITS:
        ap.add_argument(f"--{f.replace('_', '-')}", dest=f)
    ap.add_argument("--track", default="", help="comma-separated terms you want to rank for")
    a = ap.parse_args()
    data: dict = {}
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            data = json.load(f)
    fields = {f: str(data[f]) for f in LIMITS if data.get(f) is not None}
    fields.update({f: getattr(a, f) for f in LIMITS if getattr(a, f) is not None})
    track = list(data.get("track", [])) + [t for t in a.track.split(",") if t.strip()]
    if not fields:
        ap.error("give a listing.json or at least one field, e.g. --name")
    lengths, problems, tips = check(fields, track)
    print("\n".join(lengths))
    if problems:
        print("\nPROBLEMS (must fix)\n" + "\n".join(f"- {p}" for p in problems))
    if tips:
        print("\nTIPS\n" + "\n".join(f"- {t}" for t in tips))
    if not problems and not tips:
        print("\nNo problems found.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
