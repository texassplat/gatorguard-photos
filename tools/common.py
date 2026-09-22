"""Shared paths, GatorGuard vocabulary and folder hints for the photo library tools."""
import json
import math
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
WORK = ROOT / "work"           # not published: labelling inputs, caches
SITE = ROOT / "docs"           # published by GitHub Pages
SRC = Path(os.environ.get("GG_PHOTOS_SRC", Path.home() / "Desktop/GatorGuard/photos/ALL GG PHOTOS"))
DRIVE_FOLDER = "https://drive.google.com/drive/folders/1clmfBvB8T7fjUeJ5VA_deRzeleH6H0oQ"
# Unique Google Ads and Facebook ad images and videos, filed by tools/ads.py. Inventory paths
# that start with one of these folder names live here instead of in SRC.
ADS_SRC = Path(os.environ.get("GG_ADS_SRC", Path.home() / "Desktop/GatorGuard/photos/Ad assets"))
AD_FOLDERS = {"google": "Google Ads", "facebook": "Facebook Ads"}


def source_path(rel):
    """The original file for an inventory path."""
    return (ADS_SRC if Path(rel).parts[0] in AD_FOLDERS.values() else SRC) / rel


PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".psd", ".gif"}
VIDEO_EXT = {".mp4", ".mov"}
DOC_EXT = {".pdf"}
SKIP_EXT = {".lrf", ".srt", ".ds_store"}  # DJI proxy clips, drone telemetry, macOS junk

# Market centres. A photo's GPS is matched to the nearest one within MARKET_RADIUS_MI.
# Only the market name is ever published, never coordinates.
MARKETS = {
    "Indianapolis": (39.7684, -86.1581),
    "Cincinnati": (39.1031, -84.5120),
    "Columbus": (39.9612, -82.9988),
    "Louisville": (38.2527, -85.7585),
    "Detroit": (42.3314, -83.0458),
    "Grand Rapids": (42.9634, -85.6681),
    "St. Louis": (38.6270, -90.1994),
    "Fort Wayne": (41.0793, -85.1394),  # retired market, still in older photos
}
MARKET_RADIUS_MI = 75


def market_for(lat, lon):
    if lat is None or lon is None:
        return None, None
    best, dist = None, 1e9
    for name, (mlat, mlon) in MARKETS.items():
        d = haversine_mi(lat, lon, mlat, mlon)
        if d < dist:
            best, dist = name, d
    return (best if dist <= MARKET_RADIUS_MI else "Outside markets"), round(dist)


def haversine_mi(a1, o1, a2, o2):
    r = 3958.8
    p1, p2 = math.radians(a1), math.radians(a2)
    dp, dl = p2 - p1, math.radians(o2 - o1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# Flake and metallic colours. The five collections and their swatches come from the
# live /system-samples page (gatorguard-wordpress theme/gatorguard/data/samples.json);
# "folder only" colours appear as staff-named folders in the Drive but not on the site.
COLLECTIONS = {
    "Ultra": ["Chestnut", "Chestnut with Rust Red", "Granite", "Ocotillo", "Santana", "Sedona",
              "Silver Butte", "Stone Rowe"],
    "Artisan": ["Aurora", "Avalon", "Callisto", "Indigo", "Infinity", "Lunar"],
    "Bagari Stone Effects": ["Arcadia", "Bedford", "Cambridge", "Pacific Coast", "Stone Crest",
                             "Stone Ridge", "Willow", "Windermere"],
    "Vintage Mica": ["Cobalt", "Desert Edge", "Galaxy", "Gold Mine", "Gun Metal", "Shalestone",
                     "Zircon"],
    "Liquid Art": ["Cappuccino", "Charcoal", "Copper", "Crimson", "Denim", "Gray", "Sapphire"],
}
FOLDER_ONLY_COLORS = {"Black Canyon": None, "Quartzite": None, "Silverado": None, "Quarry": "Vintage Mica"}
COLOR_TO_COLLECTION = {c: coll for coll, cs in COLLECTIONS.items() for c in cs}
COLOR_TO_COLLECTION.update({c: coll for c, coll in FOLDER_ONLY_COLORS.items()})
ALL_COLORS = sorted(COLOR_TO_COLLECTION)

# Folder spellings that differ from the catalogue.
COLOR_ALIASES = {
    "chestnut rust red": "Chestnut with Rust Red",
    "stonecrest": "Stone Crest",
    "windemere": "Windermere",
    "colbalt": "Cobalt",
}


def norm_color(name):
    if not name:
        return None
    key = name.strip().lower()
    if key in COLOR_ALIASES:
        return COLOR_ALIASES[key]
    for c in ALL_COLORS:
        if c.lower() == key:
            return c
    return None


# What each top-level folder tells us. Staff named these, so they are treated as facts
# and override the vision model; the model's own read is kept for review.
FOLDER_FACTS = {
    "Basements (Full Broadcast)": {"area": "basement", "system": "full_flake"},
    "Interiors (Full Broadcast)": {"area": "interior_room", "system": "full_flake"},
    "Garages": {"area": "garage"},
    "Exterior & Porches (No Poly Enhance)": {"area_group": "exterior"},
    "Poly Enhance": {"system": "poly_enhance"},
    "Liquid Art (All Areas)": {"system": "metallic", "collection": "Liquid Art"},
    "Commercial": {"setting": "commercial"},
    "Pool Decks": {"area": "pool_deck"},
    "Solid Color Chip": {"system": "solid_color_chip"},
    "Stamped Concrete": {"system": "stamped_concrete"},
    "Tile Coated": {"system": "tile_coating"},
    "Grind & Seal": {"system": "grind_and_seal"},
    "Logo Floors": {"tags": ["custom logo"]},
    "Coating Only (Close Up)": {"shot": "close_up"},
    "Crisp Lines (Close Up)": {"shot": "close_up", "tags": ["crisp lines"]},
    "Cut Lines-Tool Joint (Close Up)": {"shot": "close_up", "tags": ["cut lines", "tool joints"]},
    "Lips (Close Up)": {"shot": "close_up", "tags": ["edge lip"]},
    "Stem Walls (Close Up)": {"shot": "close_up", "tags": ["stem wall"]},
    "Installer Photos with People": {"tags": ["installers"]},
    "Floors W- GG Truck": {"tags": ["GatorGuard truck"]},
    "Old Style Floors": {"tags": ["old style"]},
    "Gameday Photoshoot": {"tags": ["gameday photoshoot"]},
    "Employee Ad Images-Video": {"tags": ["recruiting ad"]},
    "Birdeye Assets": {"tags": ["Birdeye asset"]},
    "NFL Vikings": {"tags": ["NFL Vikings"]},
    "Gesse LA Job 2-2024": {"tags": ["Gesse LA job 2024"], "market": "Outside markets"},
    "Google Ads": {"tags": ["Google Ads"]},
    "Facebook Ads": {"tags": ["Facebook ad"]},
}

# Stage folders inside the Gesse LA job.
GESSE_STAGES = {"Grind-Prep": "surface prep (grinding)", "MME Coat": "MME base coat",
                "Color Coat - Multi-Color": "color coat", "Clear Coat": "clear coat",
                "Finals": "finished"}


def folder_facts(rel_parts):
    """Facts implied by a file's folder path (list of folder names, top first)."""
    facts = {"tags": []}
    if not rel_parts:
        return facts
    top = rel_parts[0].strip()
    for k, v in FOLDER_FACTS.get(top, {}).items():
        if k == "tags":
            facts["tags"] += v
        else:
            facts[k] = v
    for part in rel_parts[1:]:
        p = part.strip()
        color = norm_color(p)
        if color:
            facts["color"] = color
            facts["collection"] = COLOR_TO_COLLECTION.get(color)
            if facts.get("system") is None and facts["collection"] != "Liquid Art":
                facts["system"] = "full_flake"
        elif p in ("Artisan Blend", "Artisan"):
            facts["collection"] = "Artisan"
        elif p in COLLECTIONS:
            facts["collection"] = p
        if p in GESSE_STAGES:
            facts["tags"].append(GESSE_STAGES[p])
        if p == "Drone Footage" or p.startswith("DJI"):
            facts["tags"].append("drone")
        if p == "Gameday Final":
            facts["tags"].append("final pick")
        if top == "Videos" and p:
            facts["tags"].append(p)
    return facts


def file_facts(rel_path):
    """Folder facts plus a colour named by the file itself (e.g. Garages/Silverado.JPG)."""
    from pathlib import PurePath
    pp = PurePath(rel_path)
    facts = folder_facts(list(pp.parts[:-1]))
    if not facts.get("color"):
        stem = re.sub(r"[\s_-]*\(?\d+\)?$", "", pp.stem).strip()
        color = norm_color(stem)
        if color:
            facts["color"] = color
            facts["collection"] = COLOR_TO_COLLECTION.get(color)
    if facts.get("collection") and facts.get("system") is None:
        facts["system"] = "metallic" if facts["collection"] == "Liquid Art" else "full_flake"
    return facts


def slugify(s, maxlen=70):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:maxlen].rstrip("-")


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    tmp.replace(path)
