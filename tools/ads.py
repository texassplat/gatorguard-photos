#!/usr/bin/env python3
"""Download GatorGuard's Google Ads and Facebook ad images and videos, drop the duplicates,
and file the unique ones where scan.py picks them up.

    .venv/bin/python tools/ads.py fetch    # list every asset and download it -> work/ads/
    .venv/bin/python tools/ads.py dedupe   # group copies, compare with the library
                                           #   -> ADS_SRC (~/Desktop/GatorGuard/photos/Ad assets)
                                           #   -> data/ads.json
    then scan.py, derive.py, label.py and build.py as usual.

Google Ads (CID 584-488-9150): every image and YouTube video asset in the account, and the
campaigns each is linked to. Uses the shared BGI Google token (adwords scope) and
GOOGLE_ADS_DEVELOPER_TOKEN; YouTube videos download with yt-dlp.

Facebook (ad account act_4524560357645375): the account's ad image and ad video libraries,
plus every GatorGuard Page video and post its ads use. Page videos only download with the
Page's own token, which is read from /me/accounts using FACEBOOK_ADS_TOKEN (a user token).

A copy is dropped when it is the same file, the same picture resized or re-encoded, a crop
of the same picture (Google's and Meta's automatic square, portrait and vertical
versions), the same video uploaded again, or already in the Drive library. Pictures that
look alike are lined up with feature matching and compared block by block, so a version
with different text, an added logo or a different floor is kept as a separate ad and
linked to the other as a near-duplicate. Of each group of copies the most complete (not a
crop of another), largest one is kept; the rest are listed under "dropped" in
data/ads.json with the reason. Library items that ran as ads are recorded there too, so
the gallery can say so. Icons under 300px and blank images (a black square, Google's
"stock image unavailable" placeholder) are skipped.

Unnamed Google Ads images that were uploaded rather than auto-created and that no campaign
uses are set aside in ADS_SRC/GENERATED_DIR, which scan.py does not read. In September 2026
every one of them was AI-generated (people posing in made-up garages, road crews laying
erosion sock) or junk (Google logos), none showing GatorGuard's work. Pass
--include-generated to put them in the library, tagged "AI-generated" and never marked
website-ready.
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import cv2
import imagehash
import numpy as np
import requests
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from common import ADS_SRC, AD_FOLDERS, DATA, SITE, SRC, WORK, load_json, save_json

register_heif_opener()
Image.MAX_IMAGE_PIXELS = None
RAW = WORK / "ads" / "raw"
MANIFEST = WORK / "ads" / "assets.json"
PRINTS = WORK / "ads" / "prints.json"
ENV_FILES = [Path.home() / "Projects" / p / ".env.local" for p in ("", "gator-tools", "ameritech-wordpress")]

GADS_CID, GADS_MCC = "5844889150", "6538885007"
GADS_URL = f"https://googleads.googleapis.com/v23/customers/{GADS_CID}/googleAds:searchStream"
GOOGLE_TOKEN = Path.home() / ".config/claude-seo/google-oauth-token.json"
GOOGLE_AUTH = "python3 ~/Projects/seo/tools/google_auth.py"
FB = "https://graph.facebook.com/v24.0/"
FB_ACT, FB_PAGE = "act_4524560357645375", "1382027098530431"

MIN_SIDE = 300          # long side below this is an icon or favicon
MIN_SPREAD = 13         # grey-level standard deviation below this is blank or a placeholder
GENERATED_DIR = "Google Ads - AI-generated, never used"
CANDIDATE_DIST = 12     # phash bits (whole picture or centre square) worth lining up and comparing
BLOCK_LIMIT = 20        # most mean grey-level change allowed in any 1/64th of two lined-up pictures
CHANGED_LIMIT = 0.03    # most of their pixels allowed to differ noticeably
VIDEO_DIST = 10         # centre-square phash bits for two video frames to match
FRAME_AT = tuple(i / 10 for i in range(1, 10))


def env_key(name):
    if os.environ.get(name):
        return os.environ[name]
    for f in ENV_FILES:
        if f.exists():
            for line in f.read_text().splitlines():
                m = re.match(rf"\s*{name}\s*=\s*(.*)", line)
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    sys.exit(f"{name} not found in the environment or {', '.join(map(str, ENV_FILES))}")


# ---------------------------------------------------------------- Google Ads

def gads_query():
    tok = json.loads(GOOGLE_TOKEN.read_text())
    r = requests.post(tok["token_uri"], timeout=30, data={
        "client_id": tok["client_id"], "client_secret": tok["client_secret"],
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    if r.status_code != 200:
        sys.exit(f"Google token refresh failed ({r.status_code}). Re-auth: {GOOGLE_AUTH}")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}", "login-customer-id": GADS_MCC,
                      "developer-token": env_key("GOOGLE_ADS_DEVELOPER_TOKEN")})

    def q(query):
        r = s.post(GADS_URL, json={"query": query}, timeout=120)
        if r.status_code != 200:
            sys.exit(f"Google Ads query failed ({r.status_code}): {r.text[:400]}")
        return [row for chunk in r.json() for row in chunk.get("results", [])]
    return q


def list_google():
    q = gads_query()
    used = defaultdict(set)
    for res in ("campaign_asset", "ad_group_asset", "asset_group_asset", "ad_group_ad_asset_view"):
        for r in q(f"SELECT asset.id, campaign.name FROM {res} WHERE asset.type IN ('IMAGE', 'YOUTUBE_VIDEO')"):
            used[r["asset"]["id"]].add(r["campaign"]["name"])
    recs = {}
    for r in q("SELECT asset.id, asset.name, asset.type, asset.source, asset.image_asset.full_size.url, "
               "asset.image_asset.full_size.width_pixels, asset.image_asset.full_size.height_pixels, "
               "asset.youtube_video_asset.youtube_video_id, asset.youtube_video_asset.youtube_video_title "
               "FROM asset WHERE asset.type IN ('IMAGE', 'YOUTUBE_VIDEO')"):
        a = r["asset"]
        rec = {"platform": "google", "ids": [a["id"]], "auto": a.get("source") == "AUTOMATICALLY_CREATED",
               "campaigns": sorted(used.get(a["id"], ()))}
        if a["type"] == "IMAGE":
            fs = a.get("imageAsset", {}).get("fullSize", {})
            rec.update(key=f"g{a['id']}", kind="photo", name=a.get("name") or f"Google Ads image {a['id']}",
                       url=fs.get("url"), generated=not a.get("name") and not rec["auto"] and not rec["campaigns"])
        else:
            vid = a["youtubeVideoAsset"]["youtubeVideoId"]
            rec.update(key=f"y{vid}", kind="video", name=a["youtubeVideoAsset"].get("youtubeVideoTitle") or vid,
                       youtube=vid, link=f"https://www.youtube.com/watch?v={vid}")
        old = recs.get(rec["key"])
        if old:  # the same YouTube video added as two assets
            old["ids"] += rec["ids"]
            old["campaigns"] = sorted(set(old["campaigns"]) | set(rec["campaigns"]))
        else:
            recs[rec["key"]] = rec
    return list(recs.values())


# ---------------------------------------------------------------- Facebook

def fb_pages(path, token, **params):
    params.update(access_token=token)
    params.setdefault("limit", 100)
    url, out = FB + path, []
    while url:
        j = requests.get(url, params=params, timeout=120).json()
        if "error" in j:
            sys.exit(f"Facebook {path}: {j['error'].get('message', '')[:300]}")
        out += j.get("data", [])
        url, params = j.get("paging", {}).get("next"), None
    return out


def fb_ids(ids, token, fields):
    out = {}
    ids = sorted(ids)
    for i in range(0, len(ids), 50):
        j = requests.get(FB, timeout=120, params={"ids": ",".join(ids[i:i + 50]), "fields": fields,
                                                   "access_token": token}).json()
        if "error" in j:
            sys.exit(f"Facebook ids lookup: {j['error'].get('message', '')[:300]}")
        out.update(j)
    return out


def fb_date(s):
    return (s or "")[:10] or None


def list_facebook():
    user = env_key("FACEBOOK_ADS_TOKEN")
    page = next((p["access_token"] for p in fb_pages("me/accounts", user, fields="id,access_token")
                 if p["id"] == FB_PAGE), None)
    if not page:
        sys.exit("FACEBOOK_ADS_TOKEN cannot manage the GatorGuard Page, so Page videos cannot be downloaded")

    # Which campaigns use which image hash, video or Page post.
    img_use, vid_use, post_use = defaultdict(set), defaultdict(set), defaultdict(set)
    ads = fb_pages(f"{FB_ACT}/ads", user, limit=25, fields=(
        "campaign{name},creative{image_hash,video_id,effective_object_story_id,"
        "object_story_spec{link_data{image_hash,child_attachments{image_hash,video_id}},video_data{video_id}},"
        "asset_feed_spec{images{hash},videos{video_id}}}"))
    for ad in ads:
        camp = (ad.get("campaign") or {}).get("name") or "(no campaign)"
        spec = json.dumps(ad.get("creative", {}))
        hashes = re.findall(r'"(?:image_hash|hash)": "([0-9a-f]{32})"', spec)
        videos = re.findall(r'"video_id": "(\d+)"', spec)
        for h in hashes:
            img_use[h].add(camp)
        for v in videos:
            vid_use[v].add(camp)
        if not hashes and not videos and ad.get("creative", {}).get("effective_object_story_id"):
            post_use[ad["creative"]["effective_object_story_id"]].add(camp)

    recs = []
    for im in fb_pages(f"{FB_ACT}/adimages", user, fields="hash,name,url,width,height,created_time"):
        recs.append({"platform": "facebook", "key": f"fi{im['hash']}", "ids": [im["hash"]], "kind": "photo",
                     "name": im.get("name") or "untitled", "url": im.get("url"), "date": fb_date(im.get("created_time")),
                     "campaigns": sorted(img_use.get(im["hash"], ()))})

    # Page posts run as ads: their photos come in directly, their videos join the video list.
    posts = fb_ids(post_use, page, "attachments{media_type,media,target,subattachments}")
    for pid, post in posts.items():
        atts = (post.get("attachments") or {}).get("data", [])
        atts = [s for a in atts for s in ((a.get("subattachments") or {}).get("data") or [a])]
        for n, a in enumerate(atts):
            if a.get("media_type") == "video" and (a.get("target") or {}).get("id"):
                vid_use[a["target"]["id"]] |= post_use[pid]
            elif (a.get("media") or {}).get("image", {}).get("src"):
                recs.append({"platform": "facebook", "key": f"fp{pid}_{n}", "ids": [pid], "kind": "photo",
                             "name": f"Page post {pid}", "url": a["media"]["image"]["src"],
                             "campaigns": sorted(post_use[pid])})

    fields = "id,title,description,length,source,created_time,permalink_url"
    lib = {v["id"]: v for v in fb_pages(f"{FB_ACT}/advideos", user, fields=fields)}
    need = {v for v in vid_use if v not in lib} | {v for v, x in lib.items() if not x.get("source")}
    lib.update(fb_ids(need, page, fields))
    for vid, v in lib.items():
        recs.append({"platform": "facebook", "key": f"fv{vid}", "ids": [vid], "kind": "video",
                     "name": v.get("title") or f"Facebook video {vid}", "text": (v.get("description") or "")[:500],
                     "url": v.get("source"), "date": fb_date(v.get("created_time")),
                     "link": "https://www.facebook.com" + v["permalink_url"] if v.get("permalink_url") else None,
                     "auto": (v.get("title") or "").startswith("Auto_Cropped"),
                     "campaigns": sorted(vid_use.get(vid, ()))})
    return recs


# ---------------------------------------------------------------- download

EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp", "video/mp4": ".mp4",
       "video/quicktime": ".mov"}


def download(rec):
    done = next(RAW.glob(rec["key"] + ".*"), None)
    if done:
        return done.name, None
    try:
        if rec.get("youtube"):
            r = subprocess.run([str(Path(sys.executable).parent / "yt-dlp"), "-q", "--no-progress",
                                "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b", "--merge-output-format", "mp4",
                                "-o", str(RAW / f"{rec['key']}.%(ext)s"), rec["link"]], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[-300:])
            return next(RAW.glob(rec["key"] + ".*")).name, None
        if not rec.get("url"):
            raise RuntimeError("no download URL")
        with requests.get(rec["url"], timeout=600, stream=True) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").split(";")[0].strip()
            ext = EXT.get(ctype) or Path(rec["url"].split("?")[0]).suffix.lower() or ".bin"
            tmp = RAW / f"{rec['key']}.part"
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        tmp.replace(RAW / f"{rec['key']}{ext}")
        return f"{rec['key']}{ext}", None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:300]


def cmd_fetch(args):
    RAW.mkdir(parents=True, exist_ok=True)
    recs = []
    if args.only in (None, "google"):
        g = list_google()
        print(f"Google Ads: {sum(r['kind'] == 'photo' for r in g)} images, {sum(r['kind'] == 'video' for r in g)} videos")
        recs += g
    if args.only in (None, "facebook"):
        f = list_facebook()
        print(f"Facebook: {sum(r['kind'] == 'photo' for r in f)} images, {sum(r['kind'] == 'video' for r in f)} videos")
        recs += f
    if args.only:  # keep the other platform's records from the last fetch
        recs += [r for r in load_json(MANIFEST, []) if r["platform"] != args.only]
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(download, recs))
    fails = []
    for rec, (name, err) in zip(recs, results):
        rec.pop("url", None)  # signed and short-lived
        rec["file"] = name
        if err:
            rec["error"] = err
            fails.append(rec)
    save_json(MANIFEST, recs)
    size = sum(p.stat().st_size for p in RAW.iterdir()) / 1e9
    print(f"{len(recs) - len(fails)} downloaded ({size:.1f} GB in {RAW}), {len(fails)} failed")
    for r in fails[:20]:
        print("  FAIL", r["key"], r["name"][:50], r["error"])


# ---------------------------------------------------------------- fingerprints

def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def flat_rgb(im):
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def centre_square(im):
    w, h = im.size
    s = min(w, h)
    return im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))


def phash_int(im):
    im = im.copy()
    im.thumbnail((512, 512))
    return int(str(imagehash.phash(im)), 16)


def image_prints(path):
    with Image.open(path) as im:
        im.load()
        im = flat_rgb(ImageOps.exif_transpose(im))
        small = im.convert("L")
        small.thumbnail((256, 256))
        return {"w": im.width, "h": im.height, "ph": phash_int(im), "sq": phash_int(centre_square(im)),
                "sd": round(float(np.asarray(small, dtype=np.float32).std()), 1)}


def video_prints(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                        str(path)], capture_output=True, text=True)
    info = json.loads(r.stdout or "{}")
    dur = float(info.get("format", {}).get("duration") or 0)
    st = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    w, h = st.get("width"), st.get("height")
    rot = str((st.get("tags") or {}).get("rotate", ""))
    for sd in st.get("side_data_list", []) or []:
        rot = str(sd.get("rotation", rot))
    if rot.lstrip("-") in ("90", "270"):
        w, h = h, w
    frames = []
    for frac in FRAME_AT:
        f = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{dur * frac:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=512:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
        try:
            frames.append(phash_int(centre_square(Image.open(io.BytesIO(f.stdout)).convert("RGB"))))
        except Exception:
            frames.append(None)
    return {"w": w, "h": h, "dur": round(dur, 2), "frames": frames}


def prints_for(key, path, kind, cache):
    st = path.stat()
    stamp = f"{st.st_size}:{int(st.st_mtime)}:v2"
    hit = cache.get(key)
    if hit and hit.get("stamp") == stamp:
        return hit
    try:
        p = image_prints(path) if kind == "photo" else video_prints(path)
    except Exception as e:
        p = {"error": f"{type(e).__name__}: {e}"[:200]}
    p.update(stamp=stamp, sha1=sha1(path))
    return p


def bits(a, b):
    return (a ^ b).bit_count()


@lru_cache(maxsize=None)
def grey(path):
    """512px grey-scale copy and its ORB features, for lining two pictures up."""
    with Image.open(path) as im:
        im.load()
        im = flat_rgb(ImageOps.exif_transpose(im)).convert("L")
        im.thumbnail((512, 512))
    g = np.asarray(im, dtype=np.uint8)
    kp, des = cv2.ORB_create(3000).detectAndCompute(g, None)
    return g, np.float32([k.pt for k in kp]), des


def compare(path_a, path_b):
    """Line b up on a (scale, shift, slight turn) and measure what differs.

    worst: mean grey-level change in the most-changed 1/64th of the overlap. A resize or
    crop of the same picture stays low everywhere; new text, a logo or a different floor
    shows up as one or more blocks that changed a lot.
    """
    (a, pts_a, des_a), (b, pts_b, des_b) = grey(str(path_a)), grey(str(path_b))
    if des_a is None or des_b is None or len(des_a) < 10 or len(des_b) < 10:
        return None
    knn = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(des_b, des_a, k=2)
    good = [p[0] for p in knn if len(p) == 2 and p[0].distance < 0.8 * p[1].distance]
    if len(good) < 12:
        return None
    m, inliers = cv2.estimateAffinePartial2D(pts_b[[g.queryIdx for g in good]], pts_a[[g.trainIdx for g in good]],
                                             method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if m is None or inliers.sum() < 10:
        return None
    (h, w), (hb, wb) = a.shape, b.shape
    warped = cv2.warpAffine(b, m, (w, h), flags=cv2.INTER_AREA)
    mask = cv2.warpAffine(np.full(b.shape, 255, np.uint8), m, (w, h), flags=cv2.INTER_NEAREST)
    back = cv2.warpAffine(np.full(a.shape, 255, np.uint8), cv2.invertAffineTransform(m), (wb, hb),
                          flags=cv2.INTER_NEAREST)
    cover_a, cover_b = float((mask > 0).mean()), float((back > 0).mean())
    mask = cv2.erode(mask, np.ones((5, 5), np.uint8)) > 0
    if mask.sum() < 1000:
        return None
    d = np.abs(cv2.GaussianBlur(a, (5, 5), 0).astype(np.int16) - cv2.GaussianBlur(warped, (5, 5), 0).astype(np.int16))
    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    worst = 0.0
    for i in range(8):
        for j in range(8):
            sl = (slice(y0 + i * (y1 - y0) // 8, y0 + (i + 1) * (y1 - y0) // 8),
                  slice(x0 + j * (x1 - x0) // 8, x0 + (j + 1) * (x1 - x0) // 8))
            blk = mask[sl]
            if blk.sum() > 50:
                worst = max(worst, float(d[sl][blk].mean()))
    return {"changed": float((d[mask] > 24).mean()), "worst": worst, "cover_a": cover_a, "cover_b": cover_b}


def relate(a, b):
    """How two assets relate: ("same", reason, key of the one that is a crop of the other or None),
    ("variant", reason, None) for the same picture with different text or edits, or None."""
    fa, fb = a["fp"], b["fp"]
    if fa["sha1"] == fb["sha1"]:
        return "same", "same file", None
    if a["kind"] == "video":
        if not fa.get("dur") or not fb.get("dur") or abs(fa["dur"] - fb["dur"]) > max(0.75, 0.03 * max(fa["dur"], fb["dur"])):
            return None
        d = [bits(x, y) for x, y in zip(fa["frames"], fb["frames"]) if x is not None and y is not None]
        close = sum(x <= VIDEO_DIST for x in d)
        if len(d) >= 5 and close >= len(d) - 1:  # one miss allowed: a sample landing on a cut
            return "same", "re-encoded copy", None
        return ("variant", "same length, some of the same footage", None) if len(d) >= 5 and close * 2 >= len(d) else None
    if min(bits(fa["ph"], fb["ph"]), bits(fa["sq"], fb["sq"])) > CANDIDATE_DIST:
        return None
    if fa["w"] * fa["h"] < fb["w"] * fb["h"]:
        a, b = b, a
    c = compare(a["path"], b["path"])
    if c is None or max(c["cover_a"], c["cover_b"]) < 0.5:
        return None
    if c["worst"] > BLOCK_LIMIT or c["changed"] > CHANGED_LIMIT:
        return "variant", "same picture, different text or edit", None
    if c["cover_a"] > 0.9 and c["cover_b"] > 0.9:
        return "same", "resized copy", None
    inner = b["key"] if c["cover_a"] <= 0.9 and c["cover_b"] > 0.95 else a["key"] if c["cover_b"] <= 0.9 and c["cover_a"] > 0.95 else None
    return "same", "crop", inner


# ---------------------------------------------------------------- dedupe

def ad_filename(rec, ext):
    stem = Path(rec["name"]).stem if re.search(r"\.\w{3,4}$", rec["name"]) else rec["name"]
    stem = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", stem).strip(" .")[:80] or "untitled"
    tag = rec["ids"][0][:10] if rec["platform"] == "facebook" and rec["kind"] == "photo" else rec["ids"][0]
    if rec.get("youtube"):
        tag = rec["youtube"]
    return f"{stem} ({tag}){ext}"


def library_nodes(cache):
    """The Drive library as comparison nodes: photos from their web copies, videos from the originals."""
    inv = load_json(DATA / "inventory.json", [])
    jobs = []
    for it in inv:
        if Path(it["paths"][0]).parts[0] in AD_FOLDERS.values():
            continue
        if it["kind"] == "photo":
            m = next((p for p in (SITE / "m" / f"{it['id']}.jpg", SITE / "m" / f"{it['id']}.png") if p.exists()), None)
            if m:
                jobs.append({"key": f"lib:{it['id']}", "path": m, "kind": "photo"})
        elif it["kind"] == "video" and (SRC / it["paths"][0]).exists():
            jobs.append({"key": f"lib:{it['id']}", "path": SRC / it["paths"][0], "kind": "video"})
    with ThreadPoolExecutor(8) as ex:
        for j, p in zip(jobs, ex.map(lambda j: prints_for(j["key"], j["path"], j["kind"], cache), jobs)):
            cache[j["key"]] = j["fp"] = p
    missing = sum(it["kind"] == "video" for it in inv) - sum(j["kind"] == "video" for j in jobs)
    if missing:
        print(f"  note: {missing} library videos not found under {SRC}, so ad videos are not checked against them")
    return [j for j in jobs if not j["fp"].get("error")]


def cmd_dedupe(args):
    recs = [r for r in load_json(MANIFEST, []) if r.get("file")]
    if not recs:
        sys.exit("Nothing downloaded yet: run `tools/ads.py fetch` first")
    cache = load_json(PRINTS, {})
    for r in recs:
        r["path"] = RAW / r["file"]
    with ThreadPoolExecutor(8) as ex:
        for r, p in zip(recs, ex.map(lambda r: prints_for(r["key"], r["path"], r["kind"], cache), recs)):
            cache[r["key"]] = r["fp"] = p
    lib = library_nodes(cache)
    save_json(PRINTS, cache)

    dropped, nodes = [], []
    for r in recs:
        fp = r["fp"]
        if fp.get("error"):
            dropped.append({"key": r["key"], "name": r["name"], "reason": "unreadable: " + fp["error"]})
        elif r["kind"] == "photo" and max(fp["w"], fp["h"]) < MIN_SIDE:
            dropped.append({"key": r["key"], "name": r["name"], "reason": f"icon ({fp['w']}x{fp['h']})"})
        elif r["kind"] == "photo" and fp["sd"] < MIN_SPREAD:
            dropped.append({"key": r["key"], "name": r["name"], "reason": "blank or placeholder"})
        else:
            nodes.append(r)

    # Compare every ad asset with every other and with the library (library items never with each other).
    pairs = [(a, b) for i, a in enumerate(nodes) for b in nodes[i + 1:] + lib if a["kind"] == b["kind"]]
    with ThreadPoolExecutor(8) as ex:
        found = [(a, b, rel) for (a, b), rel in zip(pairs, ex.map(lambda p: relate(*p), pairs, chunksize=64)) if rel]

    parent, why, inner, variants = {}, {}, set(), []

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, (kind, reason, crop) in found:
        if kind == "variant":
            variants.append((a["key"], b["key"]))
            continue
        lib_side = " of a library item" if b["key"].startswith("lib:") else ""
        why.setdefault(a["key"], (b["key"], reason + lib_side))
        why.setdefault(b["key"], (a["key"], reason))
        if crop:
            inner.add(crop)
        ra, rb = find(a["key"]), find(b["key"])
        if ra != rb:
            parent[rb] = ra

    groups = defaultdict(list)
    for r in nodes:
        groups[find(r["key"])].append(r)
    for n in lib:
        if n["key"] in parent:
            groups[find(n["key"])].insert(0, n)

    def rank(r):
        fp = r["fp"]
        return (r["key"] not in inner, (fp.get("w") or 0) * (fp.get("h") or 0), not r.get("auto"),
                r["platform"] == "google", r["path"].stat().st_size)

    files, generated, library, rep = {}, {}, {}, {}
    for members in groups.values():
        lib_ids = [m["key"][4:] for m in members if m["key"].startswith("lib:")]
        ads = [m for m in members if not m["key"].startswith("lib:")]
        if not ads:
            continue
        info = {"platforms": sorted({m["platform"] for m in ads}),
                "campaigns": sorted({c for m in ads for c in m["campaigns"]}),
                "assets": [{k: m.get(k) for k in ("platform", "kind", "name", "ids", "date", "link", "text", "auto")
                            if m.get(k)} for m in ads]}
        keep = None
        if lib_ids:
            for lid in lib_ids:
                library[lid] = info
            for m in members:
                rep[m["key"]] = f"lib:{lib_ids[0]}"
        else:
            keep = max(ads, key=rank)
            gen = all(m.get("generated") for m in ads)
            folder = GENERATED_DIR if gen and not args.include_generated else AD_FOLDERS[keep["platform"]]
            rel = f"{folder}/{ad_filename(keep, Path(keep['file']).suffix.lower())}"
            dates = [m["date"] for m in ads if m.get("date")]
            (generated if folder == GENERATED_DIR else files)[rel] = dict(
                info, raw=keep["file"], kept=keep["key"], date=min(dates) if dates else None, generated=gen)
            for m in members:
                rep[m["key"]] = rel
        for m in ads:
            if m is not keep:
                like, reason = why[m["key"]]
                dropped.append({"key": m["key"], "name": m["name"], "reason": reason, "like": like,
                                "kept": keep["key"] if keep else None, "library": lib_ids or None})

    # Variants (same picture, different text or edit) are kept, and linked as near-duplicates.
    for a, b in variants:
        ra, rb = rep.get(a, a), rep.get(b, b)
        for x, y in ((ra, rb), (rb, ra)):
            if x != y and x in files and y not in files[x].setdefault("variants", []):
                files[x]["variants"].append(y)

    # Place the keepers (hard links when possible) and clear out files that are no longer kept.
    old = load_json(DATA / "ads.json", {})
    placed = {**files, **generated}
    for rel in (set(old.get("files", {})) | set(old.get("generated", {}))) - set(placed):
        (ADS_SRC / rel).unlink(missing_ok=True)
    for rel, f in placed.items():
        dest = ADS_SRC / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            continue
        try:
            os.link(RAW / f["raw"], dest)
        except OSError:
            shutil.copy2(RAW / f["raw"], dest)

    save_json(DATA / "ads.json", {"fetched": datetime.fromtimestamp(MANIFEST.stat().st_mtime).strftime("%Y-%m-%d"),
                                  "files": dict(sorted(files.items())), "library": library,
                                  "generated": dict(sorted(generated.items())),
                                  "dropped": sorted(dropped, key=lambda d: d["key"])})
    count = defaultdict(int)
    for rel in files:
        count[rel.split("/")[0]] += 1
    reasons = defaultdict(int)
    for d in dropped:
        reasons[d["reason"].split(" (")[0].split(":")[0]] += 1
    print(f"{len(recs)} downloaded -> {len(files)} unique kept {dict(count)} in {ADS_SRC}")
    if generated:
        print(f"{len(generated)} unused AI-generated Google images set aside in {ADS_SRC / GENERATED_DIR}")
    print(f"{len(dropped)} dropped: {dict(reasons)}")
    print(f"{len(library)} library items ran as ads; "
          f"{sum(1 for f in files.values() if f.get('variants'))} kept files have a variant (different text or edit)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--only", choices=["google", "facebook"])
    d = sub.add_parser("dedupe")
    d.add_argument("--include-generated", action="store_true",
                   help="put unused AI-generated Google images in the library too (tagged)")
    args = ap.parse_args()
    {"fetch": cmd_fetch, "dedupe": cmd_dedupe}[args.cmd](args)


if __name__ == "__main__":
    main()
