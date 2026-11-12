# Category, catalog and search pages

The category page is one native ProductsListing between the header and the footer; the catalog page is one CollectionsListing; the search page repeats the category listing. What follows comes from desktop captures of the reference sample's category and catalog pages and from their section settings. There are no phone captures of these pages: every phone statement here is inferred from the settings.

Frequencies are inflated by template copies: the sample's 42 native listings come from about 17 independent decisions, one starter listing alone accounting for about a quarter of them.

## What the platform renders

- **Filters.** The filter position defaults to `inline`: a left sidebar with the category tree, a price range, an in-stock toggle and Apply/Reset, about a quarter of the width. Settings that leave it out get it without anyone choosing it. `drawer` is a filter button over the grid and is what most designed stores write (about 3 in 4). The sidebar renders in about 1 in 4 stores, half of them by accident. Where the position lives depends on the section's settings version: current settings keep it in `filters.position`; a section stored at version 1791195496953 or older keeps the root `filtersPosition`, which the storefront moves into `filters` when it renders it. The other key is ignored, and a `filters` object written into an older section is dropped. `presets apply` writes the key the section's version reads (a new section gets the current one). Lint: `listing-filters` catches a listing whose readable key is empty or that writes the key its version ignores. Current settings always carry `filters.position`: the schema requires it, and the storefront's migration fills in `inline` where an older section left the position out. So in a current section a written `inline` does not show that anyone chose it: on a pulled page the lint reports it as info, and only the merchant can say whether the sidebar is wanted.
- **Filter behaviour.** `filters.enableAutoApply` applies a filter as soon as it changes and drops the Apply button (Reset stays); `filters.enableChips` shows the chosen filters as chips above the grid. Both are off by default. The sample predates them, so there is no count behind either: the presets leave them as the store has them. A section older than the `filters` settings cannot carry them until it is re-saved in the cabinet.
- **Card buttons.** `snippet.enableButton: disabled` hides the button whatever the theme says, `enabled` forces it, `default` follows the theme's `buttonColorInListing`. The presets write `default`: buttons are the theme card's decision, shared with the shelves (`card-lifestyle` shows none, `card-packshot` shows them, as most 1:1 stores of the sample do). Buttons together with the inline sidebar are the marketplace look.
- **Page size** is rows × columns (rows 3-6, columns 2-6, 36 at most); there is no other count. 24 per page is the most common (about 2 in 5 stores), then 18 (about 1 in 3). `snippets.layout` picks how more products load: `grid` (the default) numbers the pages, centred under the grid; `feed` adds the next page as the shopper scrolls, while search robots still get numbered pages. The sample predates `feed`, so no count stands behind it and the presets leave `layout` as the store has it. Nothing in the listing's settings sets the default sort or the H1.
- **The H1** is the category name or its cabinet SEO H1. Keep it short: a long SEO H1 wraps to two lines. Under it the listing prints the category's or collection's description from the cabinet as one markdown line (checked on a live store): fill it there with what the category is about, and do not repeat it in the hero of a category variant.
- **The card** is the theme's (`productSnippet`, `appearance.grid`); the listing only switches elements. Premium signals: a two-line card with the name above the price (`order: desc-price`, or `title` before `price` in `elementsOrder` of a newer theme; see [`theme.md`](theme.md); about 2 in 3 stores), one photo plate for the whole grid, large photos (3 columns with 3:4 photos in about 3 in 10). Price-first cards with points and split lines and two-line names read as marketplace cards. Change `productSnippet` only with the merchant's agreement: it changes every shelf too.
- **Search.** The `/search` listing is a child copy of the category listing in about 4 in 5 stores: editing one changes both. Tell the user before editing.

## Recipes

`design.py recipes list --page-type category`, `--page-type catalog`, `--page-type search`.

| Recipe | Page | For |
|---|---|---|
| `category-listing` | category | The default: the listing alone (about 3 in 5 default category pages) |
| `category-closing` | category | The listing and the store-wide subscription |
| `category-hero-variant` | category | A per-category variant with its own hero, assigned in the cabinet |
| `category-subnav` | category | A sub-category row above the grid (optional idea) |
| `category-marketplace` | category | The inline sidebar and card buttons, on purpose |
| `catalog` | catalog | The default /catalog grid (about 3 in 5 catalog pages) |
| `catalog-with-hero` | catalog | A banner above the catalog grid |
| `search` | search | The search results page |

Per-category variants: about 2 in 5 stores keep alternative category variants; a few stores give each top category a variant with its own banner, each kept in sync by hand. A variant is built like a product variant ([`product-page.md`](product-page.md#product-landings-through-alternative-page-variants)): `page add --alias categoryAndCollection` copies only the header and the footer, and the listing is added as an independent copy. Categories are assigned to it in the cabinet; a variant with nothing assigned is seen by nobody (lint `variant-dead`, info).

SEO text goes in the cabinet SEO fields, or in a RichText after the listing on a per-category variant, never on the default variant: it would repeat on every category.

## Presets

- Listing: `listing-drawer-4x6` (default, packshots or mixed photography), `listing-large-cards-3col` (3:4 model photography), `listing-gallery-5col-gapless` (objects on one plate), `listing-inline-filters` (the marketplace look, on purpose).
- Theme card to pair with: `card-packshot` or `card-lifestyle`.
- Catalog: `catalog-captioned-grid` (centred captions), `catalog-editorial-left` (left captions), `catalog-quiet-luxe` (larger regular captions), `catalog-overlay-3col` (white captions on the photo, darkening on: every overlay catalog in the sample switched it off and lost its captions on light photos).
- Category heroes: BannersCarousel `collection-hero`, `collection-hero-plate`; sub-categories: FeaturedCollections `subcategory-chips`.

## Merchant tasks (cabinet data)

Content hygiene separates a premium catalog from a default one more than section count does. List these for the merchant: seeded category names («Категория 1-го уровня»), categories with fewer products than one row, the same product photo as the cover of several categories, and `source: all` on the catalog page mixing merchandising collections into browsing. Small categories read better in 3 columns. A store with one category shows a product shelf of it instead of a one-tile catalog.

## Phones (inferred)

- Write the filter position explicitly (the key the section's version reads); `drawer` by default.
- 2 cards per phone row: every explicit listing setting in the sample does this.

## Not verified

- How the drawer and the sidebar render on phones, and the card density there.
