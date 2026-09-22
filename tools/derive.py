#!/usr/bin/env python3
"""Make the published copies and the labelling inputs for every inventory item.

    .venv/bin/python tools/derive.py [--force]

Per item (id = first 12 hex of the file's SHA-1):
  docs/t/<id>.webp   grid thumbnail, 480px wide
  docs/m/<id>.jpg    web copy, 1600px long edge (PNG kept when it has transparency);
                     for videos a poster frame, for PDFs page 1
  docs/v/<id>.mp4    videos only: muted 480p preview of the first 20 seconds
  docs/d/<id>.pdf    PDFs only: the file itself when under 15 MB
  work/label/<id>.jpg  1024px input for the vision model (videos: 2x2 frame grid)

Nothing published carries EXIF, so no GPS, camera serials or original timestamps
leave this machine. Skips outputs that already exist unless --force.
"""
import argparse
import shutil
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from common import DATA, SITE, WORK, load_json, source_path

register_heif_opener()
Image.MAX_IMAGE_PIXELS = None
T, M, V, D, L = SITE / "t", SITE / "m", SITE / "v", SITE / "d", WORK / "label"
for d in (T, M, V, D, L):
    d.mkdir(parents=True, exist_ok=True)


def flat_rgb(im):
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def has_alpha(im):
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        a = im.convert("RGBA").split()[-1]
        return a.getextrema()[0] < 250
    return False


def save_set(im, it, force, web=True):
    """Write thumb, web copy and label input from a PIL image (already upright)."""
    iid = it["id"]
    tp, lp = T / f"{iid}.webp", L / f"{iid}.jpg"
    if force or not tp.exists():
        t = flat_rgb(im)
        t.thumbnail((480, 960))
        t.save(tp, "WEBP", quality=74, method=6)
    if force or not lp.exists():
        t = flat_rgb(im)
        t.thumbnail((1024, 1024))
        t.save(lp, "JPEG", quality=85)
    if not web:
        return None
    if has_alpha(im):
        mp = M / f"{iid}.png"
        if force or not mp.exists():
            t = im.convert("RGBA")
            t.thumbnail((1600, 1600))
            t.save(mp, "PNG", optimize=True)
    else:
        mp = M / f"{iid}.jpg"
        if force or not mp.exists():
            t = flat_rgb(im)
            t.thumbnail((1600, 1600))
            t.save(mp, "JPEG", quality=80, optimize=True, progressive=True)
    return mp.name


def do_photo(it, force):
    src = source_path(it["paths"][0])
    with Image.open(src) as im:
        im.load()
        im = ImageOps.exif_transpose(im)
        return save_set(im, it, force)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:])


def do_video(it, force):
    src = source_path(it["paths"][0])
    iid, dur = it["id"], max(it.get("duration") or 0, 1)
    vp = V / f"{iid}.mp4"
    long_edge = "scale='if(gt(iw,ih),854,-2)':'if(gt(iw,ih),-2,854)'"
    if force or not vp.exists():
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-t", "20", "-an", "-vf", long_edge,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(vp)])
    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for i, frac in enumerate((0.12, 0.38, 0.62, 0.88)):
            fp = Path(tmp) / f"f{i}.jpg"
            run(["ffmpeg", "-y", "-v", "error", "-ss", f"{dur * frac:.2f}", "-i", str(src),
                 "-frames:v", "1", "-vf", "scale='if(gt(iw,ih),1280,-2)':'if(gt(iw,ih),-2,1280)'", str(fp)])
            if fp.exists():
                frames.append(Image.open(fp).convert("RGB"))
        if not frames:
            raise RuntimeError("no frames extracted")
        poster = frames[1] if len(frames) > 1 else frames[0]
        save_set(poster, it, force)
        lp = L / f"{iid}.jpg"
        if force or not lp.exists() or len(frames) > 1:
            w, h = frames[0].size
            grid = Image.new("RGB", (w * 2, h * 2), "black")
            for i, f in enumerate(frames[:4]):
                grid.paste(f.resize((w, h)), ((i % 2) * w, (i // 2) * h))
            grid.thumbnail((1024, 1024))
            grid.save(lp, "JPEG", quality=85)
    return f"{iid}.jpg"


def do_doc(it, force):
    src = source_path(it["paths"][0])
    iid = it["id"]
    with tempfile.TemporaryDirectory() as tmp:
        run(["pdftoppm", "-jpeg", "-f", "1", "-l", "1", "-scale-to", "1600", str(src), f"{tmp}/p"])
        page = next(Path(tmp).glob("p*.jpg"))
        with Image.open(page) as im:
            im.load()
            name = save_set(im, it, force)
    if it["bytes"] < 15 * 1024 * 1024:
        dp = D / f"{iid}.pdf"
        if force or not dp.exists():
            shutil.copyfile(src, dp)
    return name


def work(it, force):
    fn = {"photo": do_photo, "video": do_video, "doc": do_doc}[it["kind"]]
    try:
        return it["id"], fn(it, force), None
    except Exception as e:
        return it["id"], None, f"{type(e).__name__}: {e}"[:300]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", help="comma-separated ids")
    args = ap.parse_args()
    items = load_json(DATA / "inventory.json", [])
    if args.only:
        keep = set(args.only.split(","))
        items = [i for i in items if i["id"] in keep]
    # Videos are ffmpeg-bound; run them with fewer workers after the photos.
    errors = []
    for group, workers in (([i for i in items if i["kind"] != "video"], 12),
                           ([i for i in items if i["kind"] == "video"], 3)):
        with ProcessPoolExecutor(workers) as ex:
            futs = [ex.submit(work, it, args.force) for it in group]
            for n, f in enumerate(as_completed(futs), 1):
                iid, name, err = f.result()
                if err:
                    errors.append((iid, err))
                if n % 200 == 0:
                    print(f"  {n}/{len(group)}", flush=True)
    print(f"done: {len(items) - len(errors)} ok, {len(errors)} failed")
    for e in errors[:20]:
        print("  FAIL", e)
    sizes = {d.name: sum(p.stat().st_size for p in d.iterdir()) / 1e6 for d in (T, M, V, D)}
    print("published MB:", {k: round(v) for k, v in sizes.items()}, "total", round(sum(sizes.values())))


if __name__ == "__main__":
    main()
