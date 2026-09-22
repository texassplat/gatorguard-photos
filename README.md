# GatorGuard Photo Library

A searchable gallery of GatorGuard's "ALL GG PHOTOS" Google Drive folder plus the unique
images and videos from its Google Ads and Facebook ad accounts, published with GitHub
Pages from `docs/`. It holds web-size copies (1600px), thumbnails and short video
previews; the full-size originals stay in Drive (ad assets: in the local `Ad assets`
folder, see the end of this file), and every item shows its original path.

Drive folder (shared by John Chambers with marketing@blueglassinsights.com, 2026-02-25):
https://drive.google.com/drive/folders/1clmfBvB8T7fjUeJ5VA_deRzeleH6H0oQ

## How the labels are made

1. **Staff folder names are facts.** `tools/common.py` maps each Drive folder to what it
   says (e.g. `Basements (Full Broadcast)` means basement + full flake, a subfolder or file
   named after a colour means that colour). Only 46 files carry a staff colour: the
   Garages colour folders exist but are empty.
2. **A vision model labels everything else**: area, coating system, shot type, people and
   privacy flags, quality, website-ready, title, alt text, tags, and a plain description of
   the chip colours. Default model: Kimi K3 through OpenRouter (about 1.3 cents per item).
   `tools/label.py` also supports Claude Opus 5 through the Batch API.
3. **AI colour names are not trusted.** In a blind test on 20 staff-coloured photos,
   Kimi K3 named the exact colour 5 times. The page therefore lists colours only from
   staff folders and hand fixes (the "Color" filter) and offers "Chip colors" (grays, tans and browns,
   reds...) from the model's description. The model's closest-swatch guess shows only in
   the details panel. To trust a provider's colours, blind-test it
   (`label.py pilot --blind-only`) and add it to `TRUSTED_AI_COLOR` in `tools/build.py`.
4. **Hand fixes** go in `data/overrides.json`: `{"<id>": {"color": "Granite", "title": "..."}}`.
   They beat everything else.

## Fixing labels from the page

The gallery has an **Edit labels** button. Anyone can correct the colour, coating type,
area, title, alt text and the website-ready flag, either one picture at a time in the
details panel or in bulk: tick several pictures, then pick a colour or coating for all of
them at once. A picture with near-duplicates offers one click to copy its colour to them.

The site is static, so those edits live in that person's browser (localStorage) until the
file comes back here:

```bash
# they click "Download my edits" and send you the JSON
.venv/bin/python tools/apply_overrides.py ~/Downloads/gatorguard-photo-edits-2026-09-17.json
.venv/bin/python tools/build.py && git add -A && git commit -m "Apply label fixes" && git push
```

Merged fixes show as "set by hand" in the details panel and fill the Color filter. Several
people's files can be merged in one command; later files win. Editors can also load each
other's file with **Load an edits file** before sending one combined file back.

## Privacy

Published copies carry no EXIF, so no GPS, camera serials or original timestamps. GPS is
reduced to the nearest GatorGuard market (within 75 miles) during the scan and never
stored. The page is unlisted and blocks search engines (`noindex`, `robots.txt`), but the
URL is public: anyone with the link can see it. Labelling sends each photo to the model
provider (OpenRouter routes Kimi to third-party hosts).

## Refresh after new photos land in Drive

Download the Drive folder (Drive zips it in 2 GB parts), unzip into
`~/Desktop/GatorGuard/photos/ALL GG PHOTOS`, then:

```bash
cd ~/Projects/gatorguard-photos
.venv/bin/python tools/scan.py        # inventory, duplicates, markets  -> data/inventory.json
.venv/bin/python tools/derive.py      # thumbnails, web copies, previews -> docs/, work/label/
.venv/bin/python tools/label.py run --provider kimi --budget 40   # only unlabelled items
.venv/bin/python tools/build.py       # docs/index.html, docs/photos.csv
git add -A && git commit -m "Refresh photos" && git push
```

`label.py run` skips anything already labelled, and `--budget` stops it once total spend
for that provider reaches the figure given. `tools/refsheet.py` rebuilds the colour
reference sheet the model compares against (from the live /system-samples swatches).
Keys come from `~/Projects/.env.local` (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`).

Setup: `python3 -m venv .venv && .venv/bin/pip install pillow pillow-heif imagehash numpy anthropic openai requests yt-dlp opencv-python-headless`,
plus `ffmpeg` and `pdftoppm` on the PATH.

## Ad images and videos (Google Ads and Facebook)

`tools/ads.py` pulls every image and video from GatorGuard's ad accounts, drops the
duplicates and files the unique ones in `~/Desktop/GatorGuard/photos/Ad assets/`
(`Google Ads/` and `Facebook Ads/`), which `scan.py` reads alongside the Drive export:

```bash
.venv/bin/python tools/ads.py fetch     # list and download everything -> work/ads/raw/
.venv/bin/python tools/ads.py dedupe    # drop copies -> Ad assets/, data/ads.json
# then scan, derive, label and build as above
```

- **Google Ads** (CID 584-488-9150): every image and YouTube video asset, with the
  campaigns each is linked to. Shared BGI Google token plus `GOOGLE_ADS_DEVELOPER_TOKEN`
  (from `~/Projects/gator-tools/.env.local`); YouTube videos come down with `yt-dlp`.
- **Facebook** (ad account `act_4524560357645375`): the account's ad image and ad video
  libraries, plus the GatorGuard Page videos and posts its ads use. Needs
  `FACEBOOK_ADS_TOKEN` (a user token, read from `~/Projects/ameritech-wordpress/.env.local`
  when it is not in `~/Projects/.env.local`); the Page's own token, which Page videos
  need, is looked up with it.
- **Duplicates are dropped**: identical files, the same picture resized or re-encoded,
  crops of the same picture (the automatic square, portrait and vertical
  versions Google and Meta make), the same video uploaded again, and anything already in
  the Drive library. Look-alikes are lined up (OpenCV feature matching) and compared block
  by block, so a version with different text, an added logo or a different floor stays as
  its own item, linked as a near-duplicate. The most complete, largest copy of each group
  is kept. Icons, blank images and Google's "stock image unavailable" placeholder are
  skipped. `data/ads.json` lists what was kept, what was dropped and why, and which library
  photos ran as ads; the gallery's **Ad account** filter covers both.
- **Unused AI-generated Google images are set aside**, not added: unnamed uploads that no
  campaign uses. In September 2026 that was about 125 images of made-up people in made-up
  garages, road crews and Google logos. They sit in
  `Ad assets/Google Ads - AI-generated, never used/`; `ads.py dedupe --include-generated`
  adds them, tagged "AI-generated" and never marked website-ready.
