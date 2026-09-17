#!/usr/bin/env python3
"""Build the labelled colour reference sheet the vision model compares floors against.

    .venv/bin/python tools/refsheet.py

Swatches come from the live /system-samples page (the files named in the WordPress
theme's samples.json). Colours that exist only as staff folder names get a centre crop
of the first photo in that folder instead, captioned "(job photo)".
Writes work/ref/colors.jpg.
"""
import io
import json
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pillow_heif import register_heif_opener

from common import COLLECTIONS, FOLDER_ONLY_COLORS, SRC, WORK, norm_color

register_heif_opener()
SAMPLES = Path.home() / "Projects/gatorguard-wordpress/theme/gatorguard/data/samples.json"
SITE = "https://www.mygatorguard.com"
TILE, CAP, COLS = 190, 40, 8
OUT = WORK / "ref"
OUT.mkdir(parents=True, exist_ok=True)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


def square(im):
    return ImageOps.fit(im, (TILE, TILE), Image.LANCZOS)


def font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def main():
    samples = json.loads(SAMPLES.read_text())
    tiles = []  # (caption line 1, caption line 2, image)
    for coll in samples:
        cname = coll["label"].replace(" Collection", "")
        for s in coll["swatches"]:
            name = norm_color(s["name"]) or s["name"]
            tiles.append((name, cname, square(fetch(SITE + s["src"]))))
    for color, coll in FOLDER_ONLY_COLORS.items():
        hits = [p for p in SRC.rglob("*") if p.is_file() and p.parent.name.strip().lower() == color.lower()
                and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".heic")]
        if not hits:
            continue
        with Image.open(sorted(hits)[0]) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            w, h = im.size
            # The floor fills the lower part of a room shot; crop a square from there.
            c = int(min(w, h) * 0.35)
            crop = im.crop((w // 2 - c // 2, int(h * 0.95) - c, w // 2 + c // 2, int(h * 0.95)))
        tiles.append((color, f"{coll or 'unlisted'} (job photo)", square(crop)))

    known = {t[0] for t in tiles}
    missing = [c for cs in COLLECTIONS.values() for c in cs if c not in known]
    rows = (len(tiles) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * TILE, rows * (TILE + CAP)), "white")
    d = ImageDraw.Draw(sheet)
    f1, f2 = font(13), font(11)
    for i, (n, sub, im) in enumerate(tiles):
        x, y = (i % COLS) * TILE, (i // COLS) * (TILE + CAP)
        sheet.paste(im, (x, y))
        d.rectangle([x, y, x + TILE - 1, y + TILE + CAP - 1], outline="black")
        d.text((x + 5, y + TILE + 3), n, fill="black", font=f1)
        d.text((x + 5, y + TILE + 21), sub, fill="#444", font=f2)
    sheet.save(OUT / "colors.jpg", quality=88)
    print(f"{len(tiles)} tiles -> {OUT / 'colors.jpg'} {sheet.size}; catalogue colours without a tile: {missing}")


if __name__ == "__main__":
    main()
