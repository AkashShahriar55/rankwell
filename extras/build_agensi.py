#!/usr/bin/env python3
"""Build the two Agensi upload zips into dist/agensi/:

- app-store-listing-writer.zip: the standalone skill, works without Rankwell.
- rankwell.zip: router skill + SETUP.md + MCP config template + every Rankwell skill as a playbook.

Run from the repo root: python3 extras/build_agensi.py
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "agensi"


def add_tree(z: zipfile.ZipFile, src: Path, prefix: str) -> None:
    for f in sorted(src.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts and f.name != ".DS_Store":
            z.write(f, f"{prefix}/{f.relative_to(src)}")


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    with zipfile.ZipFile(OUT / "app-store-listing-writer.zip", "w", zipfile.ZIP_DEFLATED) as z:
        add_tree(z, ROOT / "extras" / "app-store-listing-writer", "app-store-listing-writer")
        z.write(ROOT / "LICENSE", "app-store-listing-writer/LICENSE")
    with zipfile.ZipFile(OUT / "rankwell.zip", "w", zipfile.ZIP_DEFLATED) as z:
        add_tree(z, ROOT / "extras" / "agensi-rankwell", "rankwell")
        add_tree(z, ROOT / "skills", "rankwell/playbooks")
        z.write(ROOT / "LICENSE", "rankwell/LICENSE")
    for f in sorted(OUT.glob("*.zip")):
        print(f"{f.relative_to(ROOT)}  {f.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
