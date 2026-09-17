#!/usr/bin/env python3
"""Merge inventory, folder facts, AI labels and manual fixes into the published gallery.

    .venv/bin/python tools/build.py

Writes docs/index.html (template.html with the data inlined), docs/photos.csv and
docs/robots.txt. Precedence for each field: data/overrides.json (hand fixes) >
staff folder and file-name facts > a near-duplicate's folder facts (colour only) >
the AI label. AI colour names fill the colour field only for providers listed in
TRUSTED_AI_COLOR; otherwise they are shown as a guess in the details panel.
"""
import csv
import json
from pathlib import Path

from common import (ALL_COLORS, COLOR_TO_COLLECTION, DATA, DRIVE_FOLDER, ROOT, SITE, load_json,
                    norm_color, slugify)

# Label sources in order of preference; the first one that has labelled an item wins.
PROVIDERS = ["claude", "kimi"]
# AI colour names are only trusted at these confidence levels. The 2026-09-17 blind test
# (20 photos with staff-named colours) had Kimi K3 exact on 5 of 20, so its names are
# shown as a guess only. Add a provider here once a blind test earns it.
TRUSTED_AI_COLOR = {}

# Chip colour groups, read from the model's plain-words chip description.
CHIP_WORDS = {
    "Grays": ("gray", "grey", "silver", "charcoal"),
    "Whites and creams": ("white", "cream", "ivory"),
    "Blacks": ("black",),
    "Tans and browns": ("tan", "brown", "beige", "khaki", "taupe", "mocha"),
    "Reds and rust": ("red", "rust", "maroon", "burgundy"),
    "Blues": ("blue", "navy", "teal"),
    "Golds and coppers": ("gold", "copper", "bronze", "amber", "orange"),
    "Greens": ("green",),
}

AREA = {"garage": "Garage", "basement": "Basement", "interior_room": "Interior room",
        "patio_or_porch": "Patio or porch", "pool_deck": "Pool deck", "driveway_or_walkway": "Driveway or walkway",
        "steps_or_stairs": "Steps or stairs", "commercial_or_industrial": "Commercial or industrial",
        "showroom_or_retail": "Showroom or retail", "other_exterior": "Other exterior", "not_visible": None}
SYSTEM = {"full_flake": "Full flake", "solid_color_chip": "Solid color chip", "metallic": "Metallic (Liquid Art)",
          "solid_color": "Solid color", "grind_and_seal": "Grind and seal", "poly_enhance": "Poly Enhance",
          "stamped_concrete": "Stamped concrete", "tile_coating": "Tile coating",
          "bare_concrete": "Bare concrete", "unknown": None, "not_applicable": None}
SHOT = {"finished": "Finished floor", "before": "Before", "in_progress": "In progress",
        "before_after": "Before and after", "close_up": "Close-up", "crew_working": "Crew at work",
        "team_or_portrait": "Team or portrait", "truck_or_equipment": "Truck or equipment",
        "graphic": "Graphic or logo", "document": "Document", "other": None}
KIND = {"photo": "Photo", "video": "Video", "doc": "PDF"}


def pick_area(folder, lab):
    if folder.get("area"):
        return folder["area"]
    a = lab.get("area")
    if folder.get("area_group") == "exterior" and a not in ("patio_or_porch", "pool_deck", "driveway_or_walkway",
                                                            "steps_or_stairs", "other_exterior"):
        return "patio_or_porch"
    return a


def main():
    inv = load_json(DATA / "inventory.json", [])
    overrides = load_json(DATA / "overrides.json", {})
    by_id = {it["id"]: it for it in inv}
    out, review = [], 0
    for it in inv:
        iid, folder = it["id"], it["folder"]
        rec = next((r for r in (load_json(DATA / "labels" / pv / f"{iid}.json", {}) for pv in PROVIDERS)
                    if "label" in r), {})
        lab = rec.get("label", {})
        ov = overrides.get(iid, {})

        color, color_src = folder.get("color"), "folder" if folder.get("color") else None
        if not color:
            for sid in it.get("similar", []):
                c = by_id.get(sid, {}).get("folder", {}).get("color")
                if c:
                    color, color_src = c, "duplicate"
                    break
        ai_color = lab.get("color") if lab.get("color") not in (None, "unknown", "not_applicable") else None
        if not color and ai_color and lab.get("color_confidence") in TRUSTED_AI_COLOR.get(rec.get("provider"), ()):
            color, color_src = ai_color, "ai-" + lab["color_confidence"]
        if "color" in ov:
            color, color_src = ov["color"], "manual"
        desc = (lab.get("color_description") or "").lower()
        chips = [g for g, words in CHIP_WORDS.items() if any(w in desc for w in words)]
        disagree = bool(folder.get("color") and ai_color and ai_color != folder["color"] and not rec.get("blind"))
        review += disagree

        area = ov.get("area") or pick_area(folder, lab)
        system = ov.get("system") or folder.get("system") or lab.get("system")
        if color and COLOR_TO_COLLECTION.get(color) == "Liquid Art":
            system = system or "metallic"
        shot = ov.get("shot") or folder.get("shot") or lab.get("shot_type")
        setting = ov.get("setting") or folder.get("setting") or lab.get("setting")
        if setting == "unknown":
            setting = None
        tags = []
        for t in folder.get("tags", []) + lab.get("tags", []) + ov.get("tags", []):
            t = t.strip()
            # Drop a catalogue colour name the model guessed; only staff colours are stated.
            named = norm_color(t)
            if named and named != color:
                continue
            if t and t.lower() not in [x.lower() for x in tags]:
                tags.append(t)
        name = Path(it["paths"][0]).name
        title = ov.get("title") or lab.get("title") or " ".join(
            x for x in (AREA.get(area) or "", SYSTEM.get(system) or "", color or "") if x) or Path(name).stem
        slug = slugify(ov.get("filename") or lab.get("filename") or title) or "gatorguard"
        ext = ".png" if (SITE / "m" / f"{iid}.png").exists() else ".jpg"
        row = {
            "id": iid, "k": it["kind"], "p": it["paths"], "n": name,
            "w": it.get("w"), "h": it.get("h"), "mb": round(it["bytes"] / 1e6, 1),
            "dur": it.get("duration"), "date": it.get("date"), "yr": (it.get("date") or "")[:4] or None,
            "mk": ov.get("market") or it.get("market") or folder.get("market"),
            "area": area, "set": setting, "sys": system, "col": color, "cs": color_src,
            "coll": COLOR_TO_COLLECTION.get(color) if color else folder.get("collection"),
            "aic": ai_color, "aicf": lab.get("color_confidence"), "air": lab.get("color_runner_up"),
            "cd": lab.get("color_description") or "", "chips": chips,
            "shot": shot, "ppl": lab.get("people"), "face": lab.get("faces_identifiable"),
            "priv": lab.get("privacy", []), "brand": lab.get("branding_visible"),
            "q": int(lab["quality"]) if lab.get("quality") else None, "iss": lab.get("issues", []),
            "ready": ov.get("website_ready", lab.get("website_ready")),
            "title": title, "alt": ov.get("alt_text") or lab.get("alt_text") or "", "tags": tags,
            "top": Path(it["paths"][0]).parts[0] if len(Path(it["paths"][0]).parts) > 1 else "(top level)",
            "sim": it.get("similar", []), "ai": bool(lab), "rev": disagree, "by": rec.get("model"),
            "m": f"m/{iid}{ext}", "t": f"t/{iid}.webp", "dl": f"{slug}-{iid[:4]}{ext}",
        }
        if it["kind"] == "video":
            row["v"] = f"v/{iid}.mp4"
        if it["kind"] == "doc" and (SITE / "d" / f"{iid}.pdf").exists():
            row["pdf"] = f"d/{iid}.pdf"
        out.append(row)

    labels = {"area": AREA, "sys": SYSTEM, "shot": SHOT, "k": KIND}
    meta = {"drive": DRIVE_FOLDER, "labelled": sum(1 for r in out if r["ai"]), "total": len(out),
            "colors": ALL_COLORS, "collections": COLOR_TO_COLLECTION}
    tpl = (ROOT / "template.html").read_text()
    html = (tpl.replace("/*__DATA__*/null", json.dumps(out, separators=(",", ":"), ensure_ascii=False))
               .replace("/*__LABELS__*/null", json.dumps(labels, separators=(",", ":")))
               .replace("/*__META__*/null", json.dumps(meta, separators=(",", ":"))))
    (SITE / "index.html").write_text(html)
    (SITE / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    (SITE / ".nojekyll").write_text("")
    with open(SITE / "photos.csv", "w", newline="") as f:
        cols = ["id", "k", "title", "alt", "area", "sys", "coll", "col", "cs", "cd", "shot", "set", "mk",
                "date", "q", "ready", "tags", "p", "m"]
        wr = csv.writer(f)
        wr.writerow(["id", "type", "title", "alt_text", "area", "system", "collection", "color",
                     "color_source", "chip_description", "shot_type", "setting", "market", "date",
                     "quality", "website_ready", "tags", "drive_path", "web_copy"])
        for r in out:
            wr.writerow(["; ".join(r[c]) if isinstance(r[c], list) else ("" if r[c] is None else r[c]) for c in cols])
    print(f"{len(out)} items ({meta['labelled']} AI-labelled, {review} folder/AI colour disagreements) "
          f"-> docs/index.html {round((SITE / 'index.html').stat().st_size / 1e6, 1)} MB")


if __name__ == "__main__":
    main()
