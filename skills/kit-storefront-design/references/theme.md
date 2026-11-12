# Theme (`global_settings`)

Set the theme before the sections: most of what makes a page look designed — width, vertical rhythm, gutters, heading scale, the product card — is a theme setting that every section inherits.

`kit.py … theme set` replaces the whole of `global_settings`, so always apply presets over the pulled object and write the result back:

```bash
scripts/design.py presets apply theme layout-editorial --work work.json --out gs.json
scripts/design.py presets apply theme card-lifestyle --base gs.json --out gs.json
scripts/design.py presets apply theme behaviour-defaults --base gs.json --out gs.json
kit.py … theme set --work work.json --settings-file gs.json
```

Palettes merge by `id`; everything else merges key by key, so keys the preset does not name keep the store's values.

## Platform defaults the reference stores change

| Setting | Platform default | What the 43 stores do | Recommendation |
|---|---|---|---|
| `appearance.section.vPadding` | `400` | 39 change it: 800 ×12, 1000 ×9, 500 ×9, 700 ×7 | 700-1000 (800 as a start) |
| `appearance.grid.vGap` | `0` | 39 change it: 150 ×12, 250 ×9 | 150-300 |
| `appearance.rounding.roundedType` | `normal` | sharp 30, normal 8, soft 3, slight 2 | `sharp`; `normal` or `soft` for kids, food, health, craft |
| `appearance.section.maxWidthDesktop` | `1400` | 1400 in 37 | `1400`; 1728-1920 only for gapless mosaics |
| `appearance.section.isFullscreen` | `false` | true 25, false 18 | by look: edge-to-edge gallery or boxed catalogue |
| `typography.titleSize` | `2xl` | 3xl 23, 2xl 8, 5xl 5, 4xl 4 | `3xl`; `4xl`-`5xl` for editorial and display faces |
| `typography.titleAlign` | `left` | center 26, left 17 | `center` for brand stores, `left` for catalogues and magazine layouts |
| `typography.fontFamily` | Noto Sans | kept by none; a predefined font in 29, a custom upload in 14 | change it in the cabinet theme editor (see below) |
| `productSnippet.photo.aspectRatio` | `4:3` | 3:4 ×34, 1:1 ×9, 4:3 ×0 | `3:4` for apparel on models, `1:1` for packshots and objects |
| `productSnippet.photo.enableParanja` | `true` | off in 36 | `false` |
| card order (`productSnippet.general.order`; `elementsOrder` in a newer theme, see below) | price first | title first in 30 | title before price (`desc-price`) |
| `productSnippet.effects.enableCarousel` | `none` | bullets 26, dash 8 | `bullets` (`dash` for minimal) |
| `cart.addToCartAction` | `notification` | `splitView` in 33 | `splitView` |
| `features.enableFavourites` | `false` | on in 41 | `true` |
| `delivery.enableProgress` | `true` | not counted (the sample predates the setting) | `false` unless the merchant confirmed a free-delivery threshold: on, it prints «До бесплатной доставки: N ₽» under the buy button and in the cart whenever the backend reports one |
| `collectionSnippet.aspectRatio` | `3:4` | 3:4 ×32, 1:1 ×9 | the same ratio as product photos |
| palettes | 1 | 3 is the most common count (20 stores); 41 of 95 extra palettes are never used | 3 roles: base, light alternate, dark closing |

Presets that encode these: `layout-editorial`, `layout-boxed-catalog`, `layout-soft-friendly`, `layout-narrow-editorial` (an optional idea), `palettes-three-roles`, `palette-warm-base`, `typography-centred-3xl`, `typography-centred-2xl`, `typography-left-3xl`, `typography-display-left`, `card-lifestyle`, `card-packshot`, `card-minimal-mono`, `behaviour-defaults`, `touch-gutters` (`design.py presets list theme`).

The product card (`productSnippet`) is shared by every shelf and listing: change it only with the merchant's agreement. It also decides card buttons: shelf and listing presets write `enableButton: default`, so `card-packshot` shows buttons everywhere and `card-lifestyle` nowhere, and a page never mixes the two. Split and Yandex points lines on cards stay as the store has them. On phones the theme decides the side inset (`appearance.section.hPaddingMobile`, 150-200 in about 3 in 4 themes) and the button colour of the sticky add-to-cart bar (`brandPrimary`, see [`product-page.md`](product-page.md#before-publishing-a-product-page)).

The order of the card's lines depends on the theme's settings version (`global_settings.version`). A theme at version 1790598353696 or older reads `productSnippet.general.order` (`price-desc`, the default, or `desc-price`, the title first) and `productReviews.positionInProductSnippet` (`top` or `bottom`). A newer theme reads only `productSnippet.general.elementsOrder`, the three lines `reviews`, `price` and `title` in display order (default `["reviews", "price", "title"]`), and keeps the two older keys hidden; the storefront builds `elementsOrder` from them once it moves an older theme to the newer version, replacing any `elementsOrder` stored there. A theme without a version counts as the oldest: `theme set` writes none, so a theme built from a skeleton or from plain settings is read by `order` and `positionInProductSnippet` even when it shows `elementsOrder`. The card presets write `order`; `presets apply` turns it into `elementsOrder` over a newer theme, swapping only the title and the price and leaving the reviews where the store has them, and `presets show theme <preset>` prints that shape too.

`productSnippet.effects.imageLoading` (`blur` or `neutral`, default `blur`) is required by the production schema and absent from the older one, so no preset sets it. When a card preset creates `effects` in a theme that has none, `presets apply` adds the default `blur`; where the store's theme already has `effects` without it, the constructor tolerates the gap.

## Vertical rhythm and width

- The gap between two sections is (the upper section's bottom factor + the lower section's top factor) × `vPadding` / 2, where `single` = 1, `double` = 2, `none` = 0, and a spacing token of `100` is 8 px. At `vPadding` 800 a `single` side is 32 px and a `double` side 64 px; at the default 400 a `single` side is 16 px, which is why fresh stores look cramped.
- A cramped page is fixed in the theme (raise `vPadding`), not by switching every section to `double`.
- A section's `isFullscreen` is three-state: `default` follows the theme flag; a preset that promises a look sets `enabled` or `disabled` explicitly. The text presets (long RichText, service pages, every Faq, `brand-story-split`) write `disabled`: under a theme with `isFullscreen: true` a section left at `default` is as wide as the window, and a reading column becomes a 900-1200 px line on a wide screen (`text-measure`, `story-measure`). Shelves, tiles and banners around them still span the screen.
- True edge to edge is `isFullscreen: enabled` + `hPaddingDesktop: "0"` + `hPaddingMobile: "0"`; `enabled` alone keeps the theme's side inset.
- `maxWidthDesktop` includes the side padding. A narrow editorial column is `hPaddingDesktop` 1500-1600 (about 1100 px of content).
- A box of 1728-1920 px widens every boxed section, text too: 6 RichText columns run 850-950 px. Given the store's theme (`--work`), `presets apply` fits the text presets to it (RichText and Faq to 5-6 columns, a wider side padding only where 5 columns are still too wide) and says so in `adapted`. A 50/50 ImageWithText has no setting that narrows its text column enough in such a box: keep the story within 200 characters or use the kit-custom-sections pattern `text-media-split` (`story-measure`).
- `hPaddingDesktop` / `hPaddingMobile` as an empty string is not a valid value: leave the key out to inherit.

## Palettes

- A palette is a background and a text colour; buttons take `brandPrimary`. Badges do not: each badge has its own colour, set in the badge itself (yandex-kit-cabinet `PATCH /v1/badges/{badge_id}` with `color` as `#RRGGBBAA`; checked on a live store). The platform badges come bright green and coral, which shout on a cream or muted palette: match them to the palette.
- Name palettes by role — «Светлый фон», «Тёмный фон», «Фон цвета бренда», «Акцентный текст» — so assigning them stays readable; most stores keep «Палитра цветов N».
- A brand-colour band is worth adding only when `brandPrimary` has at least 4.5:1 contrast against white text. Add it as a palette with a new id that no palette uses yet, `name`, `backgroundPrimary` = the brand colour and `text` `#FFFFFF`.
- Check text contrast (4.5:1) for every palette that carries text, and the subscription button (`brandPrimary`) on a dark band (3:1 at least).
- Cohesion lever: when product photos are cut-outs or shot on one flat backdrop, set `productSnippet.photo.fit: auto` and `backgroundColor: primary` and make the base palette background equal to the backdrop (off-white, light grey, cream). Card edges disappear and gapless grids become possible.

## Fonts

A font in `typography.fontFamily` — predefined or custom — carries uploaded file references for each weight. They cannot be looked up through the constructor, and a name written without its files breaks the text rendering. Change the font in the cabinet theme editor; this skill never writes `fontFamily`. Predefined fonts the reference stores use most: Inter, Manrope, Onest, Mulish. With condensed, mono or display faces heading sizes go up to `4xl`-`5xl`; with humanist sans they stay at `2xl`-`3xl`.

Check which font the theme uses before designing on top of it: `presets apply theme …` reports it as `font`, and `plan lint --work` prints it first (the selected custom font, else the predefined one, with the uploaded styles and weights). A starter template may ship a custom font whose files are all italics, or a display face for the body: the whole store then reads in italics. The report lists it in `open` and the lint warns (`theme-font`); ask the owner to choose an upright text font in the cabinet.

## Heading levels

The theme has one heading size, `typography.titleSize`, and every section title takes it: the page's H1 (the first titled section, see [`principles.md`](principles.md)) and every section title below it are the same size. Keep the levels apart by where the text goes:

| Level | Where it lives | Size |
|---|---|---|
| Page heading (the H1) | the first titled section under the hero: RichText `section-heading` or `lead-under-hero`, the category tiles' title, a listing's category name | `titleSize` |
| Section heading | `titleText` / `title` of a section | `titleSize`, the same |
| Sub-topic | `####` inside RichText or ImageWithText `content` (help-page `topic-label`, service-page subheadings) | markdown level, smaller |
| Lead line | `###` as the first line of a statement or a lead paragraph | markdown level |
| Body | RichText and ImageWithText text | the platform's body size (about 14-15 px); no theme or section setting changes it |

`3xl` is the sample's most common size (23 of 43) and the recipes' default, but with it every section title shouts at the page heading's size. Text-led stores (cosmetics and health, services, a blog, a help centre) read calmer at `2xl`: `typography-centred-2xl` (centred 2xl in 6 of 43 stores) instead of `typography-centred-3xl`. A long brand story at a larger body size is the kit-custom-sections pattern `text-media-split` (16 px body), not ImageWithText.

## Niche to theme

| Niche | Layout | Card | Rounding | Headings |
|---|---|---|---|---|
| Fashion, editorial apparel | `layout-editorial` or `layout-narrow-editorial` | `card-lifestyle` | sharp | `typography-centred-3xl`, or `typography-display-left` with a condensed face |
| Luxury, designer labels (an optional idea from two stores) | `layout-editorial`, `vPadding` 800-1000 | `card-lifestyle`, favourites on | sharp | centred; larger display sizes only with a display face set in the cabinet |
| Cosmetics, skincare, health | `layout-boxed-catalog` or `layout-editorial` | `card-packshot` on a neutral backdrop | sharp or normal | light neo-grotesk |
| Technical goods, large catalogues | `layout-boxed-catalog` (hairline cells: grid gaps 0 and card borders, an idea from two stores) | `card-packshot` | normal | `typography-left-3xl` |
| Handmade and design objects | `layout-editorial` with `vPadding` 1000, or a gapless mosaic | `card-packshot` on one backdrop | sharp or normal | a wide display face or mono |
| Kids, family, playful | `layout-soft-friendly` | 3:4 or 1:1 on a warm backdrop | normal or soft | a rounded sans |
| Streetwear | `layout-editorial`, gapless optional | `card-packshot`, visible buttons | sharp | `typography-display-left` |

The table leans on the sample, which is mostly fashion and beauty. For niches it lacks or holds too rarely to count (food, pets, electronics, auto parts, service businesses) see `home-other-niche`.
