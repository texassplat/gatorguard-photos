# GatorGuard Photo Library

A searchable gallery of GatorGuard's "ALL GG PHOTOS" Google Drive folder, published with
GitHub Pages from `docs/`. It holds web-size copies (1600px), thumbnails and short video
previews; the full-size originals stay in Drive, and every item shows its Drive path.

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
   staff folders ("Color (staff)") and offers "Chip colors" (grays, tans and browns,
   reds...) from the model's description. The model's closest-swatch guess shows only in
   the details panel. To trust a provider's colours, blind-test it
   (`label.py pilot --blind-only`) and add it to `TRUSTED_AI_COLOR` in `tools/build.py`.
4. **Hand fixes** go in `data/overrides.json`: `{"<id>": {"color": "Granite", "title": "..."}}`.
   They beat everything else.

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

Setup: `python3 -m venv .venv && .venv/bin/pip install pillow pillow-heif imagehash numpy anthropic openai`,
plus `ffmpeg` and `pdftoppm` on the PATH.
