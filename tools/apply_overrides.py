#!/usr/bin/env python3
"""Merge edits exported from the gallery page into data/overrides.json.

    .venv/bin/python tools/apply_overrides.py ~/Downloads/gatorguard-photo-edits-2026-09-17.json [more.json ...]
    .venv/bin/python tools/build.py       # then rebuild and push

Each file is {"<item id>": {"color": "Granite", "system": "full_flake", ...}} as the
page's "Download my edits" button writes it. Later files win over earlier ones, and
both win over what is already stored. Unknown ids or fields are reported and skipped.
"""
import sys
from pathlib import Path

from common import ALL_COLORS, DATA, load_json, save_json

FIELDS = {"color", "system", "area", "shot", "setting", "title", "alt_text", "tags",
          "filename", "market", "website_ready"}


def main(paths):
    if not paths:
        print(__doc__)
        return 1
    ids = {it["id"] for it in load_json(DATA / "inventory.json", [])}
    store = load_json(DATA / "overrides.json", {})
    added = changed = skipped = 0
    for p in paths:
        for iid, patch in load_json(Path(p).expanduser(), {}).items():
            if iid not in ids or not isinstance(patch, dict):
                skipped += 1
                continue
            clean = {}
            for k, v in patch.items():
                if k not in FIELDS:
                    print(f"  ignored field {k!r} on {iid}")
                    continue
                if k == "color" and v and v not in ALL_COLORS:
                    print(f"  ignored unknown colour {v!r} on {iid}")
                    continue
                if v == "" and k != "alt_text":
                    continue          # "not set" clears the field
                clean[k] = v
            if not clean:
                continue
            before = dict(store.get(iid, {}))
            store.setdefault(iid, {}).update(clean)
            added += iid not in ids or not before
            changed += before != store[iid]
    save_json(DATA / "overrides.json", store)
    print(f"{len(store)} items have hand fixes ({changed} changed now, {skipped} skipped). "
          f"Run tools/build.py next.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
