#!/usr/bin/env python3
"""Generate the public ``docs/`` subset from the internal source of truth.

``internal/`` (gitignored) holds the full docs. Passages cleared for publication are
wrapped in ``<!-- public:start -->`` / ``<!-- public:end -->`` markers; this script
extracts them into ``docs/<name>.md``. Because the public docs are generated purely
from tagged internal content, ``public ⊆ internal`` holds by construction.

- ``docs/`` is fully owned here: it is cleared and rewritten, so removing a tag
  removes the corresponding public file.
- If ``internal/`` is absent (e.g. a public clone), this is a no-op — the committed
  ``docs/`` is left untouched.
- ``--check`` verifies ``docs/`` is up to date without writing (exit 1 if stale).

Run:  python tools/build_public_docs.py [--check]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INTERNAL = REPO / "internal"
DOCS = REPO / "docs"

START = "<!-- public:start -->"
END = "<!-- public:end -->"


def _banner(src_name: str) -> str:
    return f"<!-- GENERATED from internal/{src_name} — do not edit; edit the internal source. -->\n"


def extract_public(text: str) -> str:
    """Return the concatenated content of all public:start/end blocks, or '' if none.

    Markers inside fenced code blocks (```/~~~) are ignored, so documented *examples*
    of the markers don't get treated as real ones.
    """
    blocks: list[str] = []
    depth = 0
    in_fence = False
    current: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            if depth > 0:
                current.append(line)
            continue
        if not in_fence and stripped == START:
            depth += 1
            continue
        if not in_fence and stripped == END:
            depth -= 1
            if depth == 0:
                blocks.append("\n".join(current).strip("\n"))
                current = []
            continue
        if depth > 0:
            current.append(line)
    return "\n\n".join(b for b in blocks if b).strip()


def render(src: Path) -> str:
    """Full generated file content for one internal source (banner + public blocks)."""
    body = extract_public(src.read_text(encoding="utf-8"))
    return _banner(src.name) + "\n" + body + "\n"


def desired_outputs() -> dict[str, str]:
    """Map docs/<name>.md -> content, for every internal doc that has public blocks."""
    out: dict[str, str] = {}
    for src in sorted(INTERNAL.glob("*.md")):
        if extract_public(src.read_text(encoding="utf-8")):
            out[src.name] = render(src)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify docs/ is up to date; do not write")
    args = ap.parse_args()

    if not INTERNAL.exists():
        print("build_public_docs: internal/ not present — nothing to do (public clone).")
        return 0

    desired = desired_outputs()
    existing = {p.name: p.read_text(encoding="utf-8") for p in DOCS.glob("*.md")} if DOCS.exists() else {}

    if args.check:
        if desired != existing:
            stale = sorted(set(desired) ^ set(existing)) or [n for n in desired if desired[n] != existing.get(n)]
            print(f"build_public_docs: docs/ is stale (differs for: {', '.join(stale)}). Run without --check.")
            return 1
        print("build_public_docs: docs/ is up to date.")
        return 0

    DOCS.mkdir(exist_ok=True)
    # docs/ is fully owned: drop generated files that no longer have a source.
    for p in DOCS.glob("*.md"):
        if p.name not in desired:
            p.unlink()
    for name, content in desired.items():
        (DOCS / name).write_text(content, encoding="utf-8")
    print(f"build_public_docs: wrote {len(desired)} file(s) to docs/: {', '.join(sorted(desired)) or '(none)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
