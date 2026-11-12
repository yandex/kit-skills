# Looks

A recipe is the sample's median: the order of sections most stores share. Built as it stands, every store comes out on the same skeleton — hero, category tiles, shelf, benefits, image and text, shelf, FAQ, subscription — with the same centred 3xl headings. A look is the other half of the decision: what this store looks like, taken from the stores of the sample that stand out rather than from the average. Pick one on step 3, write it in the plan (`"look"`) with the 2-3 moves that set the store apart, and choose the recipe's slot options by it.

Where the looks come from: the analysis of the 43 reference stores profiles each store's look in a line («quiet gallery minimalism», «monochrome brutalist», «playful illustrated»…). The six looks below group those profiles; the counts are stores whose profile reads as that look, by one reader, not a clustering of settings. The theme numbers are counted on the themes of the profiled stores. Nothing here names or reproduces one store: each look mixes several.

## How to use a look

1. Match the brief: what the store sells, how it is photographed, how large the assortment is, the brand's tone.
2. Take the look's theme presets over the recipe's (they replace the layout and heading presets, keep the card preset the photography asks for).
3. In `recipes show <id> --plan`, every slot with options is a `choose` list. Pick by the look; the slot's first option is the sample's median, not a default to keep. The header slot is the exception: it opens with a header that shows the menu as links (the burger is the sample's most common header, but a burger alone hides the categories on desktop; it is the option for a nested or long menu). Vary at least two content slots from their first option; the announcement bar, the header and the footer do not count (`rhythm-monotony` reports a plan that repeats its recipe).
4. Give home one signature block: the block that only this store has (below, per look), from the recipe's `signature` slot. Most are kit-custom-sections patterns (`type-band`, `ticker-band` or its loud two-row `running-line`, `tile-mosaic`, `stats-band`, `testimonial-spotlight`, `video-reels`); build them there. The native ones are image blocks (FeaturedCollections `lookbook-pair-bleed`, `editorial-diptych-gapless`, `captioned-diptych`, `benefit-mosaic` with the store's real facts, FeaturedProducts `gallery-shelf-gapless`); a promo strip is a banner, not a signature.
5. Presets marked *optional idea* (few stores of the sample) are allowed when the look names them.

## The looks

### Journal

Editorial chapters: big photography, a lot of air, type that carries the brand. About 10 of the 43 profiles (apparel, accessories, home textiles, a long-form single-product funnel).

- **Headings:** large and often left: 4xl-5xl in 4 of the 8 profiled themes, left in 3. `typography-display-left` with a condensed face set in the cabinet, or `typography-centred-3xl` for a quieter brand.
- **Rhythm:** `layout-editorial`, `vPadding` 800; sharp corners (4 of 8). `layout-narrow-editorial` (an optional idea) for a small range with a lot to say.
- **Palettes:** `palette-warm-base` or the base white with one dark band; colour comes from the photos.
- **Header:** `centered-logo-links` or `left-logo-inline-links`; a transparent header over a dark bleed hero on a home-only instance.
- **Hero:** `hero-bleed-live-text` (full height on phones) or `hero-bleed-image`.
- **Signature:** FeaturedCollections `lookbook-pair-bleed`, or `type-band` (a brand line set large).
- **Moves:** chapters that alternate a full-bleed image pair and a shelf; one manifesto in large type (`type-band` or `brand-statement` with a `###` lead); the story as `text-media-split` with a button.

### Gallery

Objects on one backdrop, shown like an exhibition: few words, even grids, generous space. About 6 of 43 (ceramics, jewellery, décor, furniture).

- **Headings:** small and calm: 2xl-3xl, centred or left; a mono or wide display face in the cabinet suits it. `typography-centred-2xl` or `typography-left-3xl`.
- **Rhythm:** `layout-editorial` with `vPadding` 700-1000 (3 of 5 profiled themes), sharp corners (4 of 5); the card on the backdrop colour (`card-packshot`, base palette equal to the photo backdrop, theme.md).
- **Palettes:** one: the backdrop tone as the base; a tinted light footer.
- **Header:** `centered-logo-links` or `left-logo-inline-links`.
- **Hero:** `hero-bleed-live-text` with one object, or FeaturedCollections `split-hero-panel`.
- **Signature:** `tile-mosaic` (objects in tiles of different sizes), or FeaturedProducts `gallery-shelf-gapless` as a product wall.
- **Moves:** gapless product walls instead of carousels; captions under the image, left; no banners between shelves.

### Dense catalog

A large assortment shopped by category and spec: the buy box and the search matter more than the mood. About 6 of 43 (technical goods, outdoor, department stores, beauty gadgets, mass-market kids).

- **Headings:** 3xl, centred in 4 of the 6 profiled themes; `typography-left-3xl` reads better beside filters and specs.
- **Rhythm:** `layout-boxed-catalog`; `vPadding` 600-1000; normal or sharp corners.
- **Palettes:** base white, a light tint for bands, a dark footer.
- **Header:** `catalog-two-row` (catalog button, mega menu, wide search).
- **Hero:** `hero-contained-card` inside the grid, or a short `page-masthead`.
- **Signature:** `stats-band` (range, delivery, years, real numbers) or `ticker-band` with category names.
- **Moves:** categories as a carousel right under the hero; 3-4 shelves from different sources; benefits as icon cards (`advantages`) instead of photo tiles.

### Playful

Colour, rounded shapes and short names: kids, family, gifts, pastel lifestyle. About 8 of 43.

- **Headings:** centred (5 of the 6 profiled themes), 2xl-3xl; a rounded sans in the cabinet.
- **Rhythm:** `layout-soft-friendly`; soft or normal corners (3 of 6).
- **Palettes:** `palettes-three-roles` with a pastel alternate; colour bands are welcome, grouped into 1-2 chapters.
- **Header:** `centered-logo-links`.
- **Hero:** FeaturedCollections `split-hero-panel` (a photo and a colour panel) or `hero-bleed-image`.
- **Signature:** `ticker-band` in the brand colour, or `tile-mosaic`.
- **Moves:** category names as chips on the photos (`category-label-chips`); an announcement bar in the brand tint; FAQ on a rounded card (`faq-island`, an optional idea).

### Dark drop

Streetwear, energy and manifesto brands: a night palette, loud type, short drops. About 4 of 43.

- **Headings:** loud: `typography-display-left` (5xl) with a condensed face, or centred 3xl in a heavy weight.
- **Rhythm:** `layout-editorial`, gapless where the photos allow; sharp corners.
- **Palettes:** a dark base or a dark header and footer (`dark-chrome`, which needs an inverted logo in the cabinet).
- **Header:** `centered-logo-links` on the dark palette.
- **Hero:** `video-hero-single` or `hero-bleed-live-text`.
- **Signature:** `ticker-band` (drop dates, slogans) or `video-reels`.
- **Moves:** one product wall (`gallery-shelf-gapless`) instead of two carousels; a running line between shelves; no pastel, no rounded cards.

### Premium

Quiet luxury: a small range, slow pace, materials and craft, nothing loud. About 9 of 43 (designer apparel, leather goods, skincare, candles).

- **Headings:** restrained: 2xl-3xl centred, or 4xl-5xl with a display face for a fashion label; sharp corners in all 7 profiled themes.
- **Rhythm:** `layout-editorial` with `vPadding` 800-1000 (4 of 7); a lot of air above shelves.
- **Palettes:** `palette-warm-base` (off-white, warm dark text); a tinted light footer.
- **Header:** `centered-logo-links`; the burger only for a nested menu.
- **Hero:** `hero-bleed-live-text` or a calm `hero-bleed-image`.
- **Signature:** `testimonial-spotlight` (one real review set large) or `type-band`.
- **Moves:** larger cards (`shelf-three-large`) instead of 4-across carousels; the story as `text-media-split`; FAQ in a narrow centred column (`faq-centered-narrow`, an optional idea).

## Mixing

A look is a direction, not a template: a skincare store may be premium with a dense catalog's header, a kids' store playful with a journal's story. Keep one heading scale and one alignment for the whole store, one photo ratio, and one signature block per home page.
