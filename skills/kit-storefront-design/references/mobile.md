# Phones

Native sections have no phone-specific layout, padding or type size: the phone page follows from the desktop settings. What follows comes from full-page phone captures (390 px wide) of the reference sample's home and product pages and from their section settings. There are no phone captures of category pages.

## What the platform does on phones

| Element | On a phone |
|---|---|
| Header | Always sticky. Collapses to burger, logo, search icon and cart whatever the desktop variant: favourites, profile, contacts and action titles are dropped, sitelinks and the catalog button fold into the burger, a search field becomes an icon, `searchVariant: none` removes search. An empty main menu means no burger at all. |
| Header logo | Fitted into a box of about 3:1 by `logo` size, the same on desktop and phones: `xs` 80×24 px, `sm` 90×32, `md` 120×40, `lg` 150×50, `xl` 180×60. A file wider than 4:1 comes out under 30 px tall (an 8:1 wordmark is about 15 px at `md`); a stacked or tagline logo clips. |
| Secondary menu | `modules.additional.menu` becomes a sticky, sideways-scrolling second row (+48 px; the sticky chrome then takes about 16% of the screen). An empty value has no row. What exactly triggers the row is unverified. With location on, a city pop-up may cover the first screens. |
| AnnouncementBar | One line per module, cut with an ellipsis at about 33 bold or 47 regular characters, title and body together. Contacts hidden. Not sticky. Several modules rotate. |
| BannersCarousel | Uses `mediaMobile` in the phone ratio. Heights at 390 px: 4:3 about 292 px (44% of the screen), 7:3 about 167, 5:2 about 156, 7:1 about 56, fit-screen the whole screen. The live text follows the slide's `hAlign`/`vAlign`, the same on both devices (see «Where the live text lands»). |
| `linkType: banner` + `buttonText` | The button text is never shown, on desktop or phone. |
| FeaturedProducts | `enableTwoColumnsOnTouch: true` = 2 cards per row (a carousel shows 2 and a peek); `false` = one card filling the screen, grids stack single cards. |
| FeaturedCollections | One image per tile, no phone image. A grid with `false` stacks one full-width tile per row; a carousel with `false` shows one tile and a peek; `true` shows 2 per row or view. Text baked into a tile renders at about half size. |
| ImageWithText | Always the image above the text, whatever `image.layout`; long copy runs about 1.5 screens. |
| ProductDetails | See [`product-page.md`](product-page.md): a fixed stack and a sticky add-to-cart bar without the price. |
| Footer | Menu columns with a `menuTitle` collapse into accordions (about 300 px); untitled columns print flat lists (600-900 px with two menus). Contacts are always an accordion. |
| Spacing | The only per-section phone lever is `hPaddingMobile`. There is no phone vertical padding, type size or layout for native sections: phone rhythm comes from the number and order of sections. |
| Floating widgets | Third-party chat bubbles and the city pop-up cover card buttons and the sticky bar zone. |

## Principles

1. **Budget the page in phone screens.** A home of 10 screens or fewer (the sample's median is 9, the range 4 to more than 16). Costs at 390 px: a 4:3 hero about 0.45 of a screen, a fit-screen hero 1, a carousel shelf about 0.7, a tile carousel 0.5-0.8, a product grid about 0.6 per phone row (8 cards about 1.9 screens), each stacked square tile about 0.55, each stacked 3:4 tile about 0.8. `plan lint` estimates the length from the settings and prints it for every final page, in screens of 390 × 664 CSS px (the screen without the browser bars; a 390 × 844 capture counts about a quarter fewer screens for the same page). The estimate follows the captured length of the sample's homes closely (correlation about 0.93, about one screen short at the median), but it is no bound either way: on demo stores it ran long, by about a tenth on home and by up to two thirds on short pages such as the cart. It prints what it had to assume: a catalog's category count (8) and the height of a custom section of an unknown pattern (500 px). Measure a page that sits near the budget on the preview. Lint: `touch-length`.
2. **Every banner gets its own phone art, re-composed rather than cropped.** Headline at least about 7% of the image width (28 px or more at 390), at most one short line under it, no body copy, a baked button at least 44 px tall, text inside the central 80%. Body copy shrunk from the desktop layout ends up at 7-9 px. Lint: `banner-mobile-image`, `banner-mobile-same`.
3. **Choose the phone ratio by content.** A thin strip of 4 words or fewer or a wordmark: 10:1 > 7:1 or 5:1 > 5:2. A strip with more words or two elements: a 4:3 card (`strip-to-touch-card`). A hero with baked text: 2:1 or 5:2 > 4:3. An immersive hero: fit-screen with live text or text-free art (fit-screen crops to the device; baked text near the top or bottom was cut in 4 of 10 checked fit-screen heroes). Lint: `banner-ratio-pair`.
4. **Live slide text on phones: small, anchored, darkened.** `size: sm` (`md` for titles of 20 characters or less), anchored at the bottom and centred or at the top and left, `backgroundDark` 16-24 over photos, white text, a title of 40 characters or less, a subtitle of 50 or less; the phone image leaves that band empty. The anchor advice rests on few stores: check the phone preview.
5. **Product shelves are 2-up on phones.** `enableTwoColumnsOnTouch: true` on every FeaturedProducts except a 2-card statement pair. Home shelves as carousels; grids with count 4 or 6. Lint: `shelf-touch`.
6. **Tile blocks by role.** Navigation (captioned categories): 2 per view, `true`, grid or carousel. Editorial, story, people or benefits (3 or more tiles): a carousel with `false` (one tile and a peek, about 0.8 of a screen at any count), or a grid with `true` and an even count. A text-and-photo pair: a gapless 2-column grid with `false`, which stacks in tile order. Never a grid with `false` and 3 or more tiles (an 8-tile mosaic stacks to 4-6 screens); never a 2-column carousel with `false` (the second tile shows as a sliver). Lint: `touch-stack`, `touch-sliver`.
7. **Even counts with 2 per phone row.** An odd count leaves the last card alone. Pick counts divisible by the desktop columns and by 2. Lint: `touch-odd`.
8. **Baked text in tiles halves on phones.** Headlines survive (32 px or more at desktop size), paragraphs and thin light labels do not. Prefer live titles (`text.enableTitle: true`, chips via `text.backgroundColor`, or `enableParanja: true` under white text); longer copy goes to RichText or ImageWithText.
9. **The first screen shows a way to shop.** A 4:3 hero followed directly by a captioned category row 2-up: the home pages that show a category or product on the first phone screen all do this. A full-height hero is a deliberate brand choice; then the categories come right after it. A split hero built from two tiles shows its first tile first.
10. **One row of sticky chrome.** Secondary menu empty (`header-touch-one-row`, stacked over the header's archetype preset: it leaves the logo and menu type alone), no header contacts, location off unless delivery differs by city. The second row cost the product page its price on the first screen in every checked store.
11. **A transparent header only on a home-only header instance, over a full-bleed hero whose phone image has a quiet top band (about 60 px).** Every other page's header is opaque (`header-opaque-off-home`): on a white product page a transparent header vanishes. Lint: `transparent-header`, `transparent-composite`.
12. **Product page: name and price on the first phone screen.** See [`product-page.md`](product-page.md).
13. **A short product-page tail on phones:** at most three blocks under the card; never home story grids under the product; FAQ answers closed. Lint: `pdp-tail`, `faq-expand`.
14. **Titled footer columns.** A `menuTitle` on every column saves 300-600 px on every phone page. Lint: `footer-untitled`.
15. **Side insets.** Theme `hPaddingMobile` 150-200 (12-16 px; `touch-gutters`, about 3 in 4 themes). A section `"0"` only for full-bleed media (banners, gapless tiles). Text sections below 150 put copy a few pixels from the edge. Lint: `touch-inset`.
16. **Keep the bottom 80 px free.** It belongs to the native sticky bar; a floating chat widget there covers it (a checkup item, not a constructor setting).
17. **Centre only short copy.** Centred text of 200 characters or less; longer RichText aligned left (there is no per-device alignment). ImageWithText copy of 350 characters or less, one per page. Headings up to 5xl in condensed faces did not overflow at 390 px. Lint: `long-copy` (info).
18. **Long text at a reading measure on desktop too.** A phone line is short anyway; the desktop line is set by the width: RichText over about 400 characters at `columns` 6 (about 75 characters), questions at 8, both in the theme's box (`section.isFullscreen: disabled`, not across the screen), service pages left at 6, article paragraphs left (principle 25 of [`principles.md`](principles.md)). Lint: `text-measure`.

## Where the live text lands

The hero, strip and collection presets write `slideText: {hAlign: left, vAlign: center, vOffset: 300}`; `hero-bleed-live-text` writes `center` / `bottom`. The title, the subtitle and the button land in the same place on every slide of that section:

| Image | Live text zone | Keep the product |
|---|---|---|
| Desktop 2:1, 5:2, 7:2 (`hAlign: left`, `vAlign: center`) | the left ~40% of the frame at mid-height | in the right third |
| Phone 4:3 | a band across the middle (`vAlign: bottom`: the lower band) | in the lower quarter, or out of the band |
| Phone 7:3, 5:2 | the left half; room for a title of about 5 words, no subtitle (`banner-live-text-fit`) | on the right half |
| Phone fit-screen (`hero-bleed-live-text`) | the lower third, centred | in the middle, under a quiet top band |

Generated or commissioned art follows the zone: it stays empty (no product, no face, no busy texture), and nothing is baked into it. A prompt template for image generation, per ratio:

```text
<product> on <surface/scene>, <light>, photographed for an online store banner, aspect ratio <2:1 | 4:3>.
Composition: the product in the <right third | lower quarter> of the frame; the <left 40% | middle band> is calm,
even <background colour> with no objects, no text, no logo, no people — live text will be set there.
```

Phone images are composed separately for their own zone, never cropped from the desktop file.

## Phone presets

- Banners: `hero-bleed-image` (2:1 > 4:3), `hero-bleed-live-text` (2:1 > full-height portrait with live text), `strip-to-touch-card` (5:1 > 4:3), `promo-strip-thin` (10:1 > 7:1, 4 words or fewer), `strip-7x2-promo` (7:2 > 7:3), `video-hero-single`.
- Shelves and tiles: `home-shelf-carousel`, `home-shelf-grid`, `category-carousel`, `category-label-chips`, `collection-carousel-overlay`, `split-hero-panel`, `benefit-mosaic`, `entry-tiles-pair`, `entry-tiles-trio`.
- Chrome and text: AnnouncementBar `dark-single-offer` (35 characters in total), `rotating-trust-messages`; Header `header-touch-one-row`, `header-touch-category-row`, `header-opaque-off-home`; Faq `faq-main-end`, `pdp-care-faq` (answers closed); theme `touch-gutters`.

## Phone check before publishing

Chrome
- [ ] Header main menu set (otherwise no burger); a logo file of about 3:1, measured on the 390 px preview at 30 px tall or more (a long one-line wordmark shrinks to about 15 px).
- [ ] On desktop the categories show in the header without a click (`centered-logo-links`, `left-logo-inline-links` or `catalog-two-row`); a burger alone only for a nested or long menu.
- [ ] One sticky row: secondary menu empty, or 3-5 links of 14 characters or less; no header contacts; location off unless delivery differs by city.
- [ ] A transparent header only on the home instance over a full-bleed hero with a quiet top band; every other page opaque.
- [ ] Announcement bar: each module 35 characters or less in total, no contacts. It is one store-wide section copied to the pages that show it: with 3:4 photos it should leave the product page: ask the user, then `section remove --id <bar id> --page productCard` takes off that copy alone, or `section status --status hidden --page productCard` hides it there (without `--page` both act on every copy).
- [ ] Every footer menu column has a `menuTitle`.

Home
- [ ] About 10 phone screens or fewer (`plan lint` prints the estimate); no section but the hero taller than a screen.
- [ ] Every slide has its own re-composed `mediaMobile` (text larger and stacked, no body copy, buttons 44 px or taller); fit-screen only with live text or text-free art.
- [ ] Live slide text small, anchored, darkened; titles of 40 characters or less; the product outside the live text zone on desktop and phone images; on 7:3 and 5:2 phone images a title of about 5 words and no subtitle.
- [ ] No `linkType: banner` with a `buttonText`.
- [ ] Every product shelf 2-up; home grids with count 4 or 6.
- [ ] No tile grid with `false` and 3 or more tiles; no 2-column carousel with `false`; navigation tiles 2 per view; even counts at 2 per row.
- [ ] Baked text in tiles at headline size only.
- [ ] The first phone screen shows a way to shop, or the categories come right after a full-height hero.

Product page: see [`product-page.md`](product-page.md#before-publishing-a-product-page).

## Not verified

- What triggers the header's second row: items in the secondary menu, the menu variant, or location.
- Category pages on phones (no captures).
