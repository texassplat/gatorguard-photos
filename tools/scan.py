#!/usr/bin/env python3
"""Inventory the Drive export and the ad assets: one record per unique file (by content hash).

    .venv/bin/python tools/scan.py

Reads GG_PHOTOS_SRC (default ~/Desktop/GatorGuard/photos/ALL GG PHOTOS) plus the ad assets
tools/ads.py filed under GG_ADS_SRC, and writes data/inventory.json. Exact copies (same
bytes) collapse into one record that lists
every path it was found at, so a photo filed under two folders keeps both hints.
Near-duplicates (resized or re-saved copies) are grouped later by perceptual hash.
GPS is reduced to a market name here and never stored.
"""
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import imagehash
from PIL import ExifTags, Image, ImageOps
from pillow_heif import register_heif_opener

from common import (ADS_SRC, AD_FOLDERS, DATA, DOC_EXT, PHOTO_EXT, SKIP_EXT, SRC, VIDEO_EXT,
                    file_facts, load_json, market_for, save_json)

register_heif_opener()
Image.MAX_IMAGE_PIXELS = None
GPS_TAG = 0x8825
EXIF_IFD = 0x8769


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dms(v, ref):
    try:
        d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        return None
    return -d if ref in ("S", "W") else d


def photo_meta(path):
    out = {}
    with Image.open(path) as im:
        exif = im.getexif()
        im2 = ImageOps.exif_transpose(im) if exif else im
        out["w"], out["h"] = im2.size
        base = dict(exif)
        sub = exif.get_ifd(EXIF_IFD) if exif else {}
        gps = exif.get_ifd(GPS_TAG) if exif else {}
        dt = sub.get(0x9003) or base.get(0x0132)  # DateTimeOriginal, DateTime
        if dt:
            try:
                out["date"] = datetime.strptime(str(dt).strip()[:19], "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%d")
            except ValueError:
                pass
        out["camera"] = " ".join(str(base.get(t, "")).strip() for t in (0x010F, 0x0110)).strip() or None
        if gps and 2 in gps and 4 in gps:
            lat, lon = dms(gps[2], gps.get(1)), dms(gps[4], gps.get(3))
            out["market"], out["market_mi"] = market_for(lat, lon)
        try:
            thumb = im2.convert("RGB")
            thumb.thumbnail((512, 512))
            out["phash"] = str(imagehash.phash(thumb))
        except Exception:
            pass
    return out


def iso6709(s):
    # "+39.7684-086.1581+230.000/" -> (39.7684, -86.1581)
    import re
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", s or "")
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def video_meta(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                        "-show_streams", str(path)], capture_output=True, text=True)
    info = json.loads(r.stdout or "{}")
    out = {}
    fmt = info.get("format", {})
    out["duration"] = round(float(fmt.get("duration", 0) or 0), 1)
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            w, h = s.get("width"), s.get("height")
            rot = str((s.get("tags") or {}).get("rotate", ""))
            for sd in s.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = str(sd["rotation"])
            if rot.lstrip("-") in ("90", "270"):
                w, h = h, w
            out["w"], out["h"] = w, h
            break
    ct = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
    if ct:
        out["date"] = ct[:10]
    loc = tags.get("com.apple.quicktime.location.iso6709") or tags.get("location")
    if loc:
        lat, lon = iso6709(loc)
        out["market"], out["market_mi"] = market_for(lat, lon)
    return out


def kind_of(ext):
    if ext in PHOTO_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    if ext in DOC_EXT:
        return "doc"
    return None


def scan_one(root_path):
    root, path = root_path
    ext = path.suffix.lower()
    rel = path.relative_to(root)
    rec = {"kind": kind_of(ext), "ext": ext, "bytes": path.stat().st_size, "sha1": sha1(path),
           "path": str(rel)}
    try:
        if rec["kind"] == "photo":
            rec.update(photo_meta(path))
        elif rec["kind"] == "video":
            rec.update(video_meta(path))
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:200]
    return rec


def main():
    files, skipped = [], 0
    roots = [(SRC, SRC)] + [(ADS_SRC, ADS_SRC / f) for f in AD_FOLDERS.values()]
    for root, top in roots:
        for p in sorted(top.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix.lower() in SKIP_EXT or p.name.startswith(".") or kind_of(p.suffix.lower()) is None:
                skipped += 1
                continue
            files.append((root, p))
    print(f"{len(files)} media files, {skipped} skipped (drone proxies/telemetry/system files)")
    with ThreadPoolExecutor(8) as ex:
        recs = list(ex.map(scan_one, files))

    old = {r["id"]: r for r in load_json(DATA / "inventory.json", [])}
    by_hash = {}
    for r in recs:
        item = by_hash.get(r["sha1"])
        if item is None:
            item = {k: v for k, v in r.items() if k != "path"}
            item["id"] = r["sha1"][:12]
            item["paths"] = []
            by_hash[r["sha1"]] = item
        item["paths"].append(r["path"])
    items = []
    for item in by_hash.values():
        # Folder hints from every location the file was filed under.
        merged = {"tags": []}
        for p in item["paths"]:
            f = file_facts(p)
            merged["tags"] += [t for t in f.pop("tags") if t not in merged["tags"]]
            for k, v in f.items():
                merged.setdefault(k, v)
        item["folder"] = merged
        item["added"] = old.get(item["id"], {}).get("added") or datetime.now().strftime("%Y-%m-%d")
        items.append(item)
    items.sort(key=lambda x: x["paths"][0])

    # Near-duplicate groups: identical perceptual hash (resized/re-saved copies).
    groups = {}
    for it in items:
        if it.get("phash"):
            groups.setdefault(it["phash"], []).append(it["id"])
    for ids in groups.values():
        if len(ids) > 1:
            for it in items:
                if it["id"] in ids:
                    it["similar"] = [i for i in ids if i != it["id"]]

    save_json(DATA / "inventory.json", items)
    kinds = {}
    for it in items:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    copies = sum(len(it["paths"]) - 1 for it in items)
    print(f"{len(items)} unique files {kinds}; {copies} exact copies merged; "
          f"{sum(1 for it in items if it.get('similar'))} in near-duplicate groups; "
          f"{sum(1 for it in items if it.get('market') and it['market'] != 'Outside markets')} with a market from GPS; "
          f"{sum(1 for it in items if it.get('error'))} unreadable")


if __name__ == "__main__":
    main()
