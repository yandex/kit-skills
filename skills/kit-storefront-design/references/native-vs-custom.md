# Native or custom

**Native for commerce, custom for the brand.** 36 of the 43 reference home pages are built from native sections only. A native section is edited in the cabinet by the merchant, follows the theme, survives platform updates and needs no markup review: shelves, listings, the product card, the cart, category tiles and the subscription stay native. But a home built from natives alone looks like every other home built from the same median; the brand's own face — the signature block of its look ([`looks.md`](looks.md)), a story with a button at a reading width, reviews — is welcome as a custom section (`YandexKit.Mystique`) from `kit-custom-sections`. The lint reports more than 4 custom sections on home (`custom-share`, info): beyond that the merchant edits too little in the cabinet.

## Needs a native widget covers

| Need | Native answer (widget · preset) |
|---|---|
| First screen with an image, live headline, button | BannersCarousel · `hero-bleed-image`, `hero-bleed-live-text`, `hero-contained-card` |
| Split hero: photo + text panel | FeaturedCollections · `split-hero-panel` (the panel is an image with its text) |
| Shop by category | FeaturedCollections · `category-tiles-captioned`, `category-carousel`, `category-label-chips` |
| Catalog page grid | CollectionsListing · `catalog-captioned-grid`, `catalog-overlay-3col` |
| Product shelves: new, bestsellers, sale, similar, recently viewed | FeaturedProducts · `home-shelf-carousel`, `home-shelf-grid`, `shelf-three-large`, `bento-lead` + `bento-follow`, `product-page-similar`, `recently-viewed` |
| Mid-page promo, thin offer strip, call to action with a button | BannersCarousel · `promo-between-rails`, `promo-strip-thin` (a slide with `linkType: button`) |
| Diptych, lookbook pair, gallery, social feed, benefit tiles, product picks | FeaturedCollections · `editorial-diptych-gapless`, `lookbook-pair-bleed`, `gallery-2x2-bordered`, `social-feed-grid`, `benefit-mosaic`, `product-picks` |
| Brand story, manifesto, lead text | RichText · `brand-statement`, `lead-under-hero`; ImageWithText · `brand-story-split`, `zigzag-*` |
| Delivery, payment, returns, policies | RichText · `service-page-text` + Faq · `faq-main-end` |
| FAQ | Faq · `faq-main-end`, `faq-topic-chain`, `faq-island` |
| Newsletter | EmailSubscription · `band-two-columns`, `centered-band`, `flow-into-footer` |
| Contacts with hours and address | ImageWithText · `service-info-split` |
| Legal line above the footer | RichText · `requisites-strip` |
| Loyalty programme | LoyaltyProgram · `loyalty-landing` |
| Product page blocks under the card: care, warranty, material story | Faq · `pdp-care-faq`; ImageWithText · `brand-story-split` (see [`product-page.md`](product-page.md)) |
| Logo, brand or press wall | FeaturedCollections · `logo-wall` (logos contained in 2:1 cells) |
| Entry tiles: two large tiles, then three smaller ones | BannersCarousel `hero-bleed-image`, then FeaturedCollections · `entry-tiles-pair`, then `entry-tiles-trio` |
| A video as the first screen | BannersCarousel · `video-hero-single` with video objects from `design.py media-object --video-status` |

**The product card itself stays native.** A custom card replaces ProductDetails on only a few product pages in the sample, and it drops what the platform gives the native one: the sticky add-to-cart bar on phones, the delivery estimate, Split and Plus lines, variant pickers and chips. Build custom blocks under the native card instead.

## Needs that warrant a custom section

Ask `kit-custom-sections` (`scripts/section.py patterns match "<need>"`); its catalog grows, so treat the patterns named here as examples, not the full list.

| Need | Why native cannot | Custom |
|---|---|---|
| The home's signature block | natives give the median look; the block that only this store has is markup | patterns `type-band`, `ticker-band` (or its loud two-row `running-line`), `tile-mosaic`, `stats-band`, `testimonial-spotlight`, `video-reels` by the look (natively FeaturedCollections `lookbook-pair-bleed`, `editorial-diptych-gapless`, `captioned-diptych`, `benefit-mosaic`, or FeaturedProducts `gallery-shelf-gapless`; a promo strip is a banner, not a signature) |
| A brand story with a button, 16 px text at a reading width | ImageWithText splits 50/50 (110-120 characters a line at the theme width) and has no button | pattern `text-media-split` (the recipes' default story; ImageWithText `brand-story-split` in a narrowed section as the native fallback). Its pattern page in `kit-custom-sections` opens with «native ImageWithText first»: that is the general rule of that skill; for a home or product-page story this page decides, because the story needs the button and the reading width |
| Customer reviews on the home page | no native reviews block outside the product reviews page | pattern `static-reviews`, with the merchant's real reviews |
| Running line (marquee) inside the page | the announcement bar is the only native marquee and sits above the header | pattern `running-line` |
| Before and after comparison | no native slider for two images | pattern `before-after` |
| A text-only band with a button, no image | RichText has no button; a banner needs an image | pattern `cta-band` |
| Benefit cards with icons and live text in a fixed grid | tiles need an image per card | pattern `advantages` (or FeaturedCollections `benefit-mosaic` when images exist) |
| Product shelf with tabs | FeaturedProducts has one source | pattern `goods-filters` |
| A form (wholesale request, booking) | no native form; no custom pattern either | not available: say so and offer a `mailto:` or `tel:` button in a banner |
| A countdown timer, a map of pick-up points | nothing native | not available in the pattern catalog: say so rather than fake it |

## When the answer is custom

1. Confirm there is real content for it (reviews, before/after photos).
2. Hand over to `kit-custom-sections`: it builds and lints the html + css + json_schema triple and prints the constructor commands (`templates create`, then `section add --widget YandexKit.Mystique --template-id …`).
3. Put the custom section where the recipe has the role (proof, banner, form), keep it one per role, and re-run `design.py plan lint`: more than four custom sections on home is reported (`custom-share`).
