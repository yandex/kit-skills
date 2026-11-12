# Product page

The product page is one native ProductDetails section under the header, then a short tail of shelves and at most one chapter of the store's own content. What follows comes from desktop and phone captures of the reference sample's product pages and from their section settings. Counts are shares of the sample; custom product cards (a few stores) are left out.

## What the platform renders

Settings cannot change this part; design around it.

- **Desktop, `columns-2`** (about 3 in 4 product pages): a photo grid, 2 photos per row, next to a sticky info column: breadcrumbs, title, price, a full-width add-to-cart button with a favourites square, then accordions (advantages and delivery expanded, description and characteristics collapsed).
- **Desktop, `columns-3`** (about 1 in 5): a thumbnail rail with one main photo, a middle column (short description, pickers, a characteristics preview) and a buy box on a tinted card, full tabs below. The short description prints twice.
- **Phone, both formats the same:** a swipe gallery with the next photo peeking in, breadcrumbs, badge, title, price with the old price and discount, Plus and Split lines, variant pickers, the advantages card, the delivery card, description and characteristics tabs, the description clamped with an expand link, chips in a row, then the page's other sections. **The add-to-cart button exists only as a sticky full-width bar at the bottom** in the theme's `brandPrimary`; it stays down to the footer, turns into a notify-me button when the product is out of stock, shows the amount left to free delivery whenever the backend reports a threshold and the theme's `delivery.enableProgress` is on (the default; switch it off unless the merchant confirmed a threshold), and shows no price. Every native product page captured on a phone has it.
- **Not ProductDetails settings:** the sticky bar, the sticky column, the tabs, badges (theme `features.badges`), favourites (theme `features.enableFavourites`) and reviews (a system page, left at its default in every store). `format`, `images.gap` and `section.isFullscreen` do not change the phone layout.
- **The only phone levers:** title size, product-name length, the theme photo ratio, whether an announcement bar or a second header row sits above, and whether advantages and delivery start expanded.

## What the fields mean

- `images.autoplay`, `images.loop`, `images.order` (`free`, `first`, `last`) control the **product video**: whether it plays, loops, and where it sits in the gallery. They are not photo-slider controls. Their schema defaults differ between storefront builds (off in older ones, on in newer), so the presets always write them.
- `images.initialGridCount` is the number of photos shown before «more» (2-12, default 4). In a few stores the «more» control appeared earlier than the value says; the effective rule is unverified.
- `extras.yandexDiscount` is the Yandex Pay and Plus points line (`disabled`, `s`, `m`); `extras.yandexSplit: m` is one text line with the split payment. The `widget-*` values never rendered in the sample (no store there had Split connected).
- `modules.addon` (services), `modules.bundle` (sets containing this product) and `modules.storeAvailability` (stock in physical shops, newer builds only) rendered in no store of the sample.
- `modules.characteristics.displayType: groups` exists; no store uses it.
- A few stores keep discount and Split keys inside `appearance` (an older settings version). Keep what the pulled section has.

## Recipes

`design.py recipes list --page-type product`. Each is the section order under the header.

| Recipe | For |
|---|---|
| `product-page` | Universal skeleton; start here |
| `product-image-led` | 6 or more consistent photos per product; the photo sells |
| `product-objects-gallery` | Every product photo on one backdrop; care and packaging questions |
| `product-considered` | A composition, usage or care story before buying |
| `product-few-skus` | 1-8 products; the page carries the story |
| `product-catalog-led` | Many variants and specs; very short descriptions |
| `product-playful` | Short product names, a display font, a soft theme |

Typography is chosen by the store, not left at the default. In the sample: an xl regular title with an lg semibold price on about 3 in 10 product pages (image-led brands; it also keeps the price on the first phone screen most often), the untouched 2xl medium pair on about 3 in 10 (mostly one starter template), 3xl on about 1 in 5 (loud brands with short names), a 2xl title with a 2xl bold price on about 1 in 5. Designed stores move away from the schema defaults on the gallery and the buy block, not on the modules: a gap of 100 on about 4 in 5, 8 or 12 photos on about 2 in 3, advantages expanded on almost all, chips as a carousel wherever set.

Gallery: three looks work. A seamless surface (gap 0, every photo on one backdrop, full width); an editorial grid (gap 100, 8-12 consistent 3:4 photos, full width with no top padding, the sticky info column beside it); for a small range, an infographic gallery where the photos carry the story, with a short silent product video first (`images.order: first`). It looks cheap with white cut-outs mixed with lifestyle shots, with a lone small photo in `columns-3` for an image-led brand, or with one photo on a large grey card. Full width with no top padding only with 6 or more consistent photos. For a small range, ask the merchant for infographic photos and a product video rather than adding explanatory sections.

## Tail rules

1. **The first shelf under ProductDetails is never empty on a first visit.** Similar products (`relevant`) in catalogs of about 8 products or more. In a smaller catalog `relevant` too, with the similar products set by hand for every card through **yandex-kit-cabinet** (`/v1/products/cards/{product_card_id}/similar/add`): the shelf then shows exactly those cards (checked on a live store). A curated collection is no substitute on the default product page: it is the same list on every card, so it shows the product on its own page and items that do not go with it (a cover under a pillow). It belongs only on a variant page whose products are not in it. The 8-product threshold is a working hypothesis from a few small catalogs.
2. **Recently viewed (`last_seen`) is the last shelf: never the only shelf, never the first under the card.** It is empty for a first visitor and then shows the product in view, so ad traffic sees no cross-sell. It is the only shelf on about 2 in 5 product pages of the sample and the first under the card on about 3 in 10. Lint: `pdp-tail`.
3. **A shelf's title goes in its own `titleText`.** A titled shelf hides together with its title when it is empty. A separate heading section right above an automatic shelf stays alone on the page when the shelf is empty: a few stores do this with thin baked «similar» or «recently viewed» banners, and no product-page banner in the sample uses live text. Lint: `shelf-heading-orphan`.
4. **One store-specific chapter per product page**, from the merchant's content: a care and warranty FAQ, a material or company story (ImageWithText), a technology band in the brand palette. Place it after ProductDetails or after the first shelf, never after the subscription.
5. **Grids:** `count` is a multiple of the columns. Lint: `grid-count`.
6. **No foreign template media.** Template media recur across stores of the sample, in unrelated niches. Upload the merchant's own images. For the same reason, weigh product-page evidence by independent designs, not by raw store counts.

On phones keep at most three blocks under the card (the similar shelf, recently viewed, one chapter), and never copy home story grids under the product: they multiply the phone length. FAQ answers start closed (`pdp-care-faq`; lint `faq-expand`).

## Before publishing a product page

Preview the proposal version on a phone and on a desktop.

- **Price on the first phone screen.** It is visible above the sticky bar on only about 1 in 5 product pages of the sample. With an xl title it is in about 2 in 5 stores, with 2xl in about 1 in 10, with 3xl in about 1 in 8, and never with an announcement bar on the product page. `pdp-classic-2col` carries the xl title with an lg price; over any other card preset merge `pdp-quiet-title-firm-price`, which sets the same pair. Keep one header row (`header-touch-one-row`, stacked over the header's archetype), keep names to 40 characters or less, and with 3:4 photos no announcement bar on the product page: the bar is a copy of the store-wide one, so ask the user, then take it off that page alone with `kit.py … section remove --id <bar id> --page productCard` or hide it there with `section status --id <bar id> --status hidden --page productCard` (without `--page` either command acts on every copy).
- **Delivery.** Without configured logistics the delivery module prints the delivery city and a «we do not deliver to this city» line right under the buy button: about 4 in 5 desktop captures where the module shows. If it shows, apply `pdp-modules-prelaunch` and tell the merchant to configure delivery. No store in the sample switches delivery off, so preview that result too.
- **Advantages are policy claims.** Only types the merchant confirms, each with the merchant's own `text` (`pdp-advantages-policies`): the platform wording promises cash payment, pickup points and a courier whatever the store's settings, so it stays only where all of that is true. `split-payment` only with Split connected; `fitting` and `refusal` only for wearables with try-on. Free texts copied from store to store, and try-on promised for goods nobody tries on, are common in the sample and in starter templates. `presets apply` lists every set slot in `open`; the lint warns on platform wording and template texts (`advantages-unconfirmed`). No confirmed policy: all six slots `none` or `enableAdvantages: false`.
- **Chips and breadcrumbs expose names.** The quick-filter chips list every category and collection of the product, merchandising and seeded ones included (a «Рекомендованные товары» collection on most captures, «Категория 1-го уровня» on several). If any name is technical, switch the chips off (`pdp-modules-prelaunch`) or ask the merchant to rename it. Breadcrumbs cannot be switched off: the category name is fixed in the cabinet.
- **Button colour.** The sticky bar and card buttons use `brandPrimary` with a label colour the platform picks. Near-black and deep brand colours read clearly; pastels give a washed-out button, and white labels on dusty pinks fell under 3:1 in a couple of stores. Check 4.5:1 against the label before a theme change; a pastel brand keeps the pastel for tints and uses a deep shade for `brandPrimary`.
- **Title length.** One or two lines at the chosen size: 60 characters or less at 2xl, 40 or less when the price must reach the first phone screen.

## Product landings through alternative page variants

A product can get its own page: an alternative `productCard` variant. About 1 in 3 stores of the sample keep such variants.

Mechanics:

- Products are assigned to a variant in the merchant's cabinet, not through the constructor. After `page add`, tell the merchant; on the next pull check the variant's product count (lint `variant-dead`, info).
- A section on several pages is stored once: the `parent` copy carries the settings, `child` copies on other pages repeat it with the same section id, and `usual` sections belong to their page. `section set` on a parent rewrites every child.
- `page add --alias productCard` copies only the header and the footer. The product card, the shelves and the subscription are added one by one with `section add --page-id`, and each is an **independent copy**: it does not follow later edits of the default page, and the default page does not follow it. Say so to the user.
- The card stays at the top of the page, under the header. A variant without a card shows no photos, price or buy button; the lint refuses it (`product-card-missing`).

The sequence (the constructor's commands; `<page id>` is the `page_id` that `page add` prints, `<card id>` the default page's ProductDetails):

```bash
kit.py … page add --work work.json --alias productCard --title "<variant name>"
kit.py … schema widget --name YandexKit.ProductDetails --skeleton > pd-schema.json
scripts/design.py presets apply ProductDetails pdp-classic-2col --work work.json --section-id <card id> --schema pd-schema.json --as-new --out pd.json
kit.py … section add --work work.json --page-id <page id> --widget YandexKit.ProductDetails --settings-file pd.json --position 2
kit.py … section add --work work.json --page-id <page id> --widget YandexKit.ImageWithText --settings-file story.json --position 3
scripts/design.py plan lint --work work.json --page <page id>
```

`--as-new` builds the new card from the default one: its settings over the store's skeleton, without `version` and without the empty-string paddings the cabinet writes, which `section add` refuses in a new section. It copies the card as it is, module presets included. An archetype preset over the copy (`pdp-classic-2col` in the sequence above, when the default card was built differently) rewrites the modules the default card had set: delivery switched back on and expanded, chips back. So either drop the preset from the `--as-new` line and keep the copy as it is, or apply every module preset of the default card again after it (`pdp-modules-prelaunch`, `pdp-advantages-policies`…), then compare the two cards' `modules` keys in the reports before `section add`. The shelves go in the same way (`dataSource` copied from the default page's shelves). A new shelf must carry `dataSource.id`, which live shelves mostly lack: `--as-new` writes the type for `relevant` and `last_seen` (checked on a live store) and leaves a marker in `open` for a category or collection, whose id comes from yandex-kit-cabinet. A subscription added here is an independent copy too, not the store-wide one: check it on the proposal preview.

| Need | Use |
|---|---|
| Per-product text (composition, usage, care) | No variant: the product description and characteristics, plus one shared section on the default page. Variants per product drift apart as they are copied. |
| One product needs a short story | A variant: its own copy of the card, one or two blocks of the store's content, the default shelves, the subscription. |
| A product line shares a story | One variant for the whole line, every product of it assigned there. |

Not possible through the constructor yet: making the variant's ProductDetails (or a shelf) a linked child of the default page's section, so the card stays identical everywhere. That link is set in the cabinet.

These are examples, not recipes: each rests on one or two stores of the sample, so do not route to them by niche.

**A long product landing** (an optional idea from two stores): product card, the problem it solves, how and when to use it, why it works, results over time, composition proof, social proof, FAQ. Natively: the card, a RichText lead, `section-heading` + tiles, ImageWithText, a heading + a tile carousel, a 2:1 banner, reviews (custom), `faq-main-end` with 5-10 real questions. Minimum: card, usage, proof, FAQ, on one palette. Use it on the default product page only when the store has one real product. It deliberately breaks two rules of this page, at most three blocks under the card and one chapter: say so to the user, and on phones keep the answers closed and the chapters short. Typical defects of such landings: platform example answers, one question asked several times, an empty RichText used as a spacer, placeholder titles hidden instead of filled, a fit-screen banner with baked text cropped on desktop. The publish gate catches the first three (`platform-placeholder`, `faq-repeated`, `richtext-empty`).

## Not verified

- Whether collapsing advantages and delivery brings the phone description up by about a screen.
- What `images.initialGridCount` really controls.
- Whether a `relevant` or `last_seen` shelf with the literal id (`"relevant"`, `"last_seen"`) renders; the schema requires an id, live shelves mostly carry none.
- How the product page renders with `enableDelivery: false`.
- The rule the platform uses to pick the button label colour.
- Whether a variant's own copy of the card renders exactly like the default card; compare both on the proposal preview.
