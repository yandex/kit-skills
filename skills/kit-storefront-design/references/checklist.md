# Pre-publish review

Run it on the proposal version (`kit.py … content push --work work.json --commit-message "..." --no-publish --confirm`), on a desktop screen and on a phone, before the confirmed push that publishes. The constructor prints the proposal's version number, not a link: the owner opens it in the cabinet, in the storefront constructor's version history, by that number, and checks the layout there on both screen sizes. What only the published storefront shows (the page's H1 and meta tags, the delivery line and the chips of a product page, the real contrast of a badge or a button) is checked right after the publish, or from `plan lint`, which prints the H1. That push needs the user's confirmation like a publish: a server without proposal support publishes the edit and the constructor exits `4`. Then the design is already live: tell the user at once and run this review on the storefront itself.

## Machine check

```bash
scripts/design.py plan lint --work work.json --page main --page 404 --page cart
scripts/design.py plan lint --work work.json --all-pages      # service pages, variants, landings, store-wide sections
```

No errors on any page you changed; after touching a page variant or a store-wide section (header, footer, bar, subscription), none on any page. Every remaining warning is either fixed or explained to the user.

## Content

- [ ] Every text on the page is the merchant's or neutral interface wording («Каталог», «Недавно смотрели»). No offer, price, discount, delivery or return promise the merchant did not give.
- [ ] A discount is mentioned only where a real promocode is linked; the announcement bar, the subscription and the delivery page tell the same story. The lint checks this for the subscription only: read the bar, banner titles and texts by hand.
- [ ] Every image is the store's own upload; banners have a separate mobile image; nothing important is cropped on the phone.
- [ ] Headlines are live text where the recipe asks for it; text baked into images stays in the central 80% and carries no prices.
- [ ] Category and collection tiles, banners and buttons open real pages; nothing points at the 404 page.
- [ ] Service pages linked from the header or the footer (delivery, payment, returns, details, policies) carry the merchant's text, not the template.
- [ ] The footer shows only contacts that are filled in the cabinet; no all-zero phone.
- [ ] Every published static page you designed has its own SEO title and description written from the store's facts (lint `seo-meta-empty`); none promises bonuses, instalments or cashback the store does not offer.
- [ ] The H1 of each page (`plan lint` prints it) says what the page sells and sits in the first two content sections, never the subscription (lint `page-h1`).

## Composition

- [ ] One hero, right under the header; one slide unless the store is multi-brand retail.
- [ ] Category entry within the first two content sections of home.
- [ ] Image moments and shopping grids alternate; no two same-shaped shelves in a row; every shelf has its own source.
- [ ] One brand-story block in the middle third; at most one or two proof blocks, all real.
- [ ] The subscription is the last content section before the footer; the footer and the closing band share or contrast palettes on purpose.
- [ ] 8-14 content sections on home, or a recipe's declared reason for fewer.
- [ ] An empty cart, the 404 page, the catalog and the blog lead to products: a shelf a first visitor sees (lint `page-dead-end`).

## Theme and look

- [ ] Theme presets applied before sections; sections leave title alignment and width at `default` unless the preset says otherwise.
- [ ] Every title on a page has one alignment (lint `title-align-mixed`): a Faq centred under a centred theme, a service page left as a whole.
- [ ] The theme font (printed by `plan lint --work`) is an upright text face; italics or a display face for the body go to the owner for the cabinet (lint `theme-font`).
- [ ] Long text at 60-80 characters a line: long RichText at 6 columns, questions at 8, both in the theme's box (`section.isFullscreen: disabled` under a theme that spans sections across the screen; 5-6 columns in a 1728-1920 px box), article paragraphs left (lint `text-measure`, `story-measure`).
- [ ] One photo ratio across tiles, shelves and the listing.
- [ ] Badge colours (set per badge in the cabinet, not by the theme) sit with the palette: the default bright green and coral shout on a muted one.
- [ ] The body sits on the base palette; coloured bands are few and grouped; text contrast is at least 4.5:1 on every palette used for text.
- [ ] On desktop the categories show in the header without a click; the logo is at least 30 px tall on desktop and phone.
- [ ] Header and footer look like a pair (full width or boxed together: the archetype presets and the footer presets all follow the theme width); a transparent header only over an edge-to-edge hero on a home-only header.

## Product page

Details and the reasons: [`product-page.md`](product-page.md#before-publishing-a-product-page).

- [ ] Name and price visible above the sticky bar on the first phone screen: an xl regular title with an lg price (`pdp-quiet-title-firm-price`, already in `pdp-classic-2col`), names of 40 characters or less, one header row, no bar on the product page with 3:4 photos (the bar is a store-wide copy: taking it off one page is a cabinet step, so ask the user).
- [ ] No «we do not deliver to this city» line under the buy button; otherwise `pdp-modules-prelaunch` and a task for the merchant.
- [ ] Advantages are only policies the merchant confirmed, each in the merchant's own words (lint `advantages-unconfirmed`); the same in the cart; the chips show no technical names.
- [ ] No «До бесплатной доставки: N ₽» under the buy button unless the merchant set that threshold (theme `delivery.enableProgress`).
- [ ] `brandPrimary` against the button label: 4.5:1 or more.
- [ ] The first shelf under the card is not recently viewed, and recently viewed is not the only shelf; no heading section above an automatic shelf.
- [ ] At most three blocks under the card on phones; FAQ answers closed; no media reused from another store's template.
- [ ] Every alternative product page has its own product card (lint `product-card-missing`) and products assigned in the cabinet; the user knows its sections are independent copies of the default page's.

## Category, catalog and search

Details: [`listing.md`](listing.md).

- [ ] The filter position written on every listing (`filters.position`; the root `filtersPosition` in a section stored at version 1791195496953 or older), `drawer` unless the marketplace look is chosen on purpose.
- [ ] Editing the category listing changes the search page too: the user knows.
- [ ] Seeded category names, categories shorter than a row and one photo reused as several covers are listed for the merchant.

## Phone

Run the phone check in [`mobile.md`](mobile.md#phone-check-before-publishing) on the proposal preview.

## Hand-off

- [ ] The user has seen the `content push --dry-run` plan and the proposal preview, and confirmed the push that publishes.
- [ ] Anything left for the cabinet is listed for the user: menus, logo, contacts, font, promocodes, page variants assigned to categories and products.
