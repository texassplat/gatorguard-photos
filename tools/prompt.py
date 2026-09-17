"""The labelling instructions and output schema, shared by every model provider."""
from common import ALL_COLORS, COLLECTIONS, FOLDER_ONLY_COLORS

COLOR_ENUM = ALL_COLORS + ["unknown", "not_applicable"]
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["content", "area", "setting", "system", "color", "color_confidence", "color_runner_up",
                 "color_description", "shot_type", "people", "faces_identifiable", "privacy",
                 "branding_visible", "quality", "issues", "website_ready", "title", "alt_text",
                 "tags", "filename"],
    "properties": {
        "content": {"type": "string", "enum": [
            "floor_photo", "before_after_composite", "marketing_graphic", "logo_or_text_graphic",
            "document_or_screenshot", "people_or_team", "vehicle_or_equipment", "other"]},
        "area": {"type": "string", "enum": [
            "garage", "basement", "interior_room", "patio_or_porch", "pool_deck", "driveway_or_walkway",
            "steps_or_stairs", "commercial_or_industrial", "showroom_or_retail", "other_exterior",
            "not_visible"]},
        "setting": {"type": "string", "enum": ["residential", "commercial", "unknown"]},
        "system": {"type": "string", "enum": [
            "full_flake", "solid_color_chip", "metallic", "solid_color", "grind_and_seal",
            "poly_enhance", "stamped_concrete", "tile_coating", "bare_concrete", "unknown",
            "not_applicable"]},
        "color": {"type": "string", "enum": COLOR_ENUM},
        "color_confidence": {"type": "string", "enum": ["high", "medium", "low", "none"]},
        "color_runner_up": {"type": "string", "enum": COLOR_ENUM},
        "color_description": {"type": "string"},
        "shot_type": {"type": "string", "enum": [
            "finished", "before", "in_progress", "before_after", "close_up", "crew_working",
            "team_or_portrait", "truck_or_equipment", "graphic", "document", "other"]},
        "people": {"type": "string", "enum": ["none", "background", "prominent"]},
        "faces_identifiable": {"type": "boolean"},
        "privacy": {"type": "array", "items": {"type": "string", "enum": [
            "license_plate", "house_number", "customer_face", "personal_papers", "vehicle_interior"]}},
        "branding_visible": {"type": "boolean"},
        "quality": {"type": "string", "enum": ["1", "2", "3", "4", "5"]},
        "issues": {"type": "array", "items": {"type": "string", "enum": [
            "blurry", "dark", "overexposed", "cluttered", "low_resolution", "tilted",
            "text_overlay", "heavy_filter", "wet_or_dirty"]}},
        "website_ready": {"type": "boolean"},
        "title": {"type": "string"},
        "alt_text": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "filename": {"type": "string"},
    },
}

collections_text = "\n".join(f"- {c}: {', '.join(cs)}" for c, cs in COLLECTIONS.items())
SYSTEM = f"""You label the photo library of GatorGuard Concrete Coatings, a company that installs \
coated concrete floors (garages, basements, patios, pool decks, commercial floors) in the US Midwest. \
Staff will search these labels to find pictures for the website, ads and social posts, so accuracy \
matters more than completeness: say "unknown" rather than guess.

Coating systems (field "system"):
- full_flake: a colored base fully covered with broadcast vinyl chips ("flakes"), then clear-coated. Speckled, terrazzo-like look.
- solid_color_chip: full flake done with chips of essentially one color.
- metallic: GatorGuard's "Liquid Art" metallic epoxy. Marbled, swirled, reflective, no chips.
- solid_color: a flat single-color coating with no chips.
- grind_and_seal: ground bare concrete with a clear sealer.
- poly_enhance: GatorGuard's "Poly Enhance" clear wet-look sealer over existing (often exterior or decorative) concrete.
- stamped_concrete: patterned stamped concrete that has been coated or sealed.
- tile_coating: coating applied over tile.
- bare_concrete: uncoated concrete (typical of before shots).

Flake and metallic colors come from these collections:
{collections_text}
- Colors staff use that are not on the website: {', '.join(FOLDER_ONLY_COLORS)}.
The first image in the conversation is a labelled reference sheet of the swatches (the last tiles are \
cropped job photos, a rougher guide). Colors differ mainly in the mix of chip colors (tan/brown/black/\
red/gray/white/blue/copper) and chip size. Lighting, wet clear coat and distance shift appearance a lot, \
so use color_confidence honestly: "high" only when the chip mix clearly matches one swatch and no \
other; "none" when no floor color is visible. Set color "not_applicable" when there is no coated floor \
(graphics, documents, bare concrete). color_description always says, in plain words, what the chips \
or metallic look like (e.g. "tan, brown and black chips, medium size"), or "" when not applicable.

Folder facts: staff filed each picture in folders. The user message lists the folder path and any \
facts it implies. Treat stated facts as correct unless the picture plainly contradicts them (for \
example a logo graphic filed under Garages), and let the folder name inform tags.

Other fields:
- shot_type: finished = a completed floor shown off; before = the space before coating; in_progress = \
prep, grinding, repair or coating underway; close_up = detail of the surface, edge, joint or stem wall.
- people: prominent when a person is a main subject. faces_identifiable when any face could be recognised.
- privacy: list anything that should not be published as is (readable license plate, house number, \
a customer's face, personal papers, car interior). Crew in GatorGuard shirts are not customers.
- quality: 5 = sharp, well lit, well composed, ready for a homepage; 3 = usable; 1 = unusable.
- website_ready: true only for quality 4-5 finished or striking photos with no privacy issue and no clutter.
- title: 3 to 8 words, specific (e.g. "Three-car garage with tan full flake floor").
- alt_text: at most 125 characters. Describe what is visible first; add at most one search phrase \
(e.g. "full flake garage floor coating") and only when the picture proves it. Never name a color \
unless the folder facts give it or your color_confidence is high. No brand claims, no marketing adjectives.
- tags: 3 to 10 lowercase search words or short phrases a staff member would type (surface, look, \
room features, colors of chips, objects such as cars or cabinets, stage).
- filename: a lowercase-hyphenated slug of 3 to 8 words describing the picture, no extension.
Never use em dashes in any text field. For a video you receive four frames in a 2x2 grid; label the \
video as a whole. For a PDF you receive its first page."""

INTRO = "Reference sheet of GatorGuard flake and metallic colors (name, then collection)."
