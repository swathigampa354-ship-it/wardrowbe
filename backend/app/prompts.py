"""Prompts, copied from the upstream ``app/prompts/*.txt`` and trimmed.

``clothing_analysis.txt`` is reused verbatim (minus the two fields the trial
does not store). ``recommendation.txt`` loses the preference/learning and
mandatory-item sections, which no longer exist server-side.
"""

CLOTHING_PROMPT = """OUTPUT ONLY JSON. NO TEXT. NO DESCRIPTIONS. NO EXPLANATIONS.

Analyze the MAIN clothing item in this image. Use ONLY these values:

TYPE (required, pick one):
shirt, t-shirt, top, polo, blouse, tank-top, sweater, cardigan, hoodie, knit,
pants, jeans, shorts, skirt, dress, jumpsuit, blazer, jacket, coat, vest,
shoes, sneakers, boots, sandals, hat, scarf, belt, tie, socks, bag, accessories

SUBTYPE (optional): A more specific name within the type. Examples:
- shirt -> henley, button-down, oxford, flannel, hawaiian, camp-collar
- pants -> chinos, joggers, cargo, trousers, leggings, sweatpants
- dress -> sundress, slip-dress, maxi, midi, wrap, shirt-dress, a-line
- jacket -> denim-jacket, bomber, parka, windbreaker, trucker, anorak
- shoes -> loafers, oxfords, mules, flats, heels, platforms
- sneakers -> low-top, high-top, chunky, slip-on
- boots -> ankle, chelsea, combat, knee-high, rain
- skirt -> mini, midi, maxi, pleated, wrap, pencil
- sweater -> pullover, crewneck, turtleneck, v-neck
Use null if the type is specific enough (e.g. jeans, hoodie, blazer).

PRIMARY_COLOR (required, pick one):
black, white, gray, navy, blue, light-blue, red, burgundy, pink, green, olive,
yellow, orange, purple, brown, tan, beige, cream, gold, silver

PATTERN (required, pick one):
solid, striped, plaid, checkered, floral, graphic, geometric, polka-dot,
camouflage, animal-print

MATERIAL (optional, pick one or null):
cotton, denim, leather, wool, polyester, silk, linen, knit, fleece, suede,
velvet, nylon, canvas

FORMALITY (required, pick one):
very-casual, casual, smart-casual, business-casual, formal

STYLE (pick 1-2 that best describe):
casual, classic, sporty, minimalist, bohemian, preppy, streetwear, elegant,
athletic, vintage, modern, rugged

SEASON (pick all that apply):
spring, summer, fall, winter, all-season

FIT (optional, pick one or null):
slim, regular, relaxed, oversized, tailored, cropped

Output this exact JSON structure (a single object, NOT wrapped in an array):
{"type":"TYPE","subtype":null,"primary_color":"COLOR","colors":["COLOR1"],"pattern":"PATTERN","material":null,"formality":"FORMALITY","style":["STYLE1"],"season":["SEASON1"],"fit":null}"""

CLOTHING_DESCRIPTION_PROMPT = """Describe the clothing item in this photo in ONE short sentence
(10-20 words). Start with the colour, then the garment and any notable detail.
No preamble, no quotes, no markdown. Example:
"Navy unstructured linen blazer with patch pockets and a soft shoulder line.\""""

OUTFIT_PROMPT = """You are an expert fashion stylist creating complete outfits.

CONTEXT:
- Occasion: {occasion}
- Season: {season}
{weather_text}- Note: {note_text}

STYLING PRINCIPLES:

Color coordination — use ONE of these approaches:
- Monochrome: same color family in different shades
- Neutral base + accent: build on neutrals (black, white, gray, navy, beige,
  brown, cream) with one color pop
- Analogous: neighboring colors that harmonize (blue + teal, olive + khaki)
- Limit to 3 distinct colors maximum. Neutrals do not count toward this limit.

Texture and fabric:
- Contrast textures for depth; do not double the same texture
- Match fabric weight to the weather

Proportion and silhouette:
- Balance volume: a relaxed top wants a more fitted bottom, and vice versa
- Never go oversized on both top and bottom

Layering (when the weather calls for it):
- base (shirt/tee) -> mid (sweater/cardigan/light jacket) -> outer (coat)
- The outfit must still work with the outer layer removed

AVAILABLE ITEMS (each line starts with its number):
{items_text}

RULES:
- Pick exactly ONE top + ONE bottom + ONE shoes, OR ONE dress + ONE shoes
- OPTIONAL: one bag, and accessories that genuinely add something
- Add outerwear only if the weather or occasion calls for it
- Every piece must earn its place — never add items to fill slots
- Do NOT use the same item number twice
- Respond with valid JSON containing exactly 3 distinct outfits, each a complete look.
  First outfit = strongest recommendation. Vary palettes and energy across the three.

{{"outfits":[{{"items":[1,5,9],"headline":"Short catchy title, max 5 words","highlights":["One short sentence each — vary your reasoning across color, texture, proportion, occasion, weather"],"styling_tip":"One specific, actionable styling detail"}}]}}"""
