# Page plans and the lint

A plan is the page you intend to build, written down before anything is sent to the store. `design.py plan lint` checks it against the principles a machine can check; `plan lint --work` does the same for a page already in the pulled workspace.

## Format

```json
{
  "page": "main",
  "page_type": "home",
  "recipe": "home-fashion",
  "stage": "draft",
  "palettes": ["default", "palette-light", "palette-dark"],
  "sections": [
    {"widget": "YandexKit.Header", "preset": "centered-logo-links", "role": "header"},
    {"widget": "YandexKit.BannersCarousel", "preset": "hero-bleed-image", "role": "hero"},
    {"widget": "YandexKit.FeaturedCollections", "preset": "category-carousel", "role": "categories",
     "settings": {"modules": [{"title": "<fill: category name>", "linkSource": {"link": "/catalog/<fill: slug>", "linkId": "<fill: id>"}}]}},
    {"widget": "YandexKit.FeaturedProducts", "preset": "home-shelf-carousel", "role": "shelf",
     "settings": {"dataSource": {"type": "collection", "slug": "<fill: slug>", "id": "<fill: id>"}}},
    {"widget": "YandexKit.Mystique", "pattern": "static-reviews", "role": "proof"},
    {"widget": "YandexKit.EmailSubscription", "preset": "band-two-columns", "role": "subscribe"},
    {"widget": "YandexKit.Footer", "preset": "dark-footer-columns", "role": "footer"}
  ]
}
```

- `sections` are in page order. `widget` is required; everything else is optional.
- `preset` names a preset of that widget; the lint merges its settings under the section's own `settings`, so a plan only spells out what differs.
- `settings` holds the key settings you already know: sources, links, texts, modules. Write a value you do not have yet as `<fill: what it is>`.
- `role` is what the section does on the page. The lint infers it when absent (the first banner is the hero, a subscription is `subscribe`, and so on); state it when the widget plays a different part, e.g. two tiles used as the hero. Roles: `announcement`, `header`, `footer`, `hero`, `categories`, `tiles`, `shelf`, `banner`, `break`, `story`, `text`, `heading`, `proof`, `faq`, `subscribe`, `closing`, `listing`, `catalog`, `product`, `cart`, `loyalty`, `custom`, `gallery`, `service`, `form`, `signature`.
- `id` (or `"existing": true`) marks a section the plan keeps from the store rather than adds: the rules for what a plan adds (`shelf-source-id`, `listing-filters` as an error) treat it as pulled, so a kept shelf never gets an invented `dataSource.id`.
- `pattern` names the kit-custom-sections pattern for a planned custom section; `template_id` is filled once it exists. A custom section that stands in for a system widget takes its role: `header` and `footer` count as chrome, not content, and `product` is the page's product card. `plan lint --work` reads the role from the template's alias (`YandexKit.Header`, `YandexKit.Footer`, `YandexKit.ProductDetails`, `YandexKit.ProductsListing`).
- `recipe` names the recipe the plan follows: a warning that recipe declares in its `deviations` says so, with the reason, and stays a warning.
- `theme` is either a subset of the store's `global_settings` (as `plan lint --work` passes it) or the recipe's theme preset names (as `recipes show --plan` writes them); the lint reads the card and the phone rhythm from it.
- `palettes` lists the palette ids of the store's theme; with it the lint catches a section pointing at a palette that does not exist.
- `meta` (the page's `seo_title`, `seo_description`, `seo_h1`), `alias` and `page_id` come with `plan lint --work`: with them the lint checks the page's own SEO fields (`seo-meta-empty`). A custom section from the workspace carries `heading`: `h1` when its template prints a literal h1, `title` when it prints a section title; `template_schema`, the template's json_schema, so an empty image the template does not require is not reported; and `template_alias`, the widget it stands in for. A custom page (no alias) gets its type from its address (`/about`, `/faq`, `/contacts`, `/lookbook`, `/promo…`, `/opt`); `plan lint --work … --page <id> --page-type <type>` sets it for an address that does not tell. A variant made with `page add` carries no product or category counts: the lint then asks to check the assignment (`variant-dead`, info).
- A final or pulled page reports its H1: the first section that prints a title, unless a listing, a product card, the cart or the blog list is on the page (those win it). `plan lint` prints it as `H1:`.
- `page_type` is one of: home, category, catalog, product, cart, 404, about, lookbook, contacts, service, faq, b2b, promo, loyalty, search, reviews, bundle, blog, system, other. `plan lint --work` derives it from the page alias (`productCard` is product, `privacy` is service, `unsubscribe` is system, and so on).

`design.py recipes show <id> --plan [--with-optional]` prints the skeleton of a recipe in this format. A recipe slot with interchangeable options comes as `{"slot": "story", "role": "story", "choose": [{"widget": …, "preset" or "pattern": …, "when": …}, …]}`: the first option is the sample's median (for the header, a header with links: the burger is the option for a nested menu), the others what the looks ask for. Replace the `choose` list with the option you pick (`widget`, `preset` or `pattern`), keeping `slot`. Until then the lint reads the slot as its first option and lists it as «to fill»; a final plan with an open choice is an error. A home plan also names its look (`"look"`: a look of [`looks.md`](looks.md) and the 2-3 moves that set the store apart).

## Stages

- **draft** (default for a plan file): composition rules apply; `<fill>` markers, missing media and custom sections without a template are counted as «to fill», not errors.
- **final** (`--final`, and always for `--work`): the page is about to be published, so every marker, empty image, banner slide without an image, shelf without a source, empty RichText and custom section without a template is an error (an empty image of a custom section only warns: the template may declare it optional). A final page also reports its estimated phone length.

A plan is what you are about to write, so a shelf in it must carry `dataSource.id` (`shelf-source-id`) and a listing its filter position (`listing-filters`: `filters.position` for a new section, which `section add` stamps with the current settings version; for a section the plan keeps, the key its version reads, or either key when the plan does not show the version). A pulled page (`--work`) is partly the store's untouched drift, which renders and which the constructor tolerates: there `listing-filters` only warns (and reports a current listing on `inline` as info: that value is the default, not proof of a choice) and `shelf-source-id` is not checked. Store-wide sections (header, footer, bar, subscription) report their phone advice once, on home, not on every page that copies them. A page the store keeps hidden renders nothing: its errors come as warnings marked «hidden page», to fix before that page is published (a plan can say `"hidden": true`). A section the store keeps hidden on a published page renders nothing either, and `plan lint --work` leaves it out.

## Exit codes

`0` no errors (warnings allowed) · `2` errors, or warnings with `--strict` · `3` usage · `5` page not found in the workspace.

A warning is a principle a designer may break on purpose; when you keep one, tell the user why. The recipes that break a principle by design say so in their `deviations`.

How severities were set: every rule was run over the reference sample of 43 designed stores and its hit rate measured. An **error** is what breaks rendering or the schema, what shows a placeholder, a duplicate or an unbacked promise (two shelves on one source show the same row: `shelf-source-repeat` stays an error on about 1 in 6 designed homes), or a composition rule that fires on fewer than 1 in 10 designed stores. A rule that fires on more of them is a **warning** at most, and **info** where it fires on most of them without evidence of harm (`empty-enum`, `variant-dead`, `long-copy`). The link rules (`tile-links`, `banner-links`) stay warnings although most designed stores break them: the principle goes against the sample on purpose. So do `page-h1`, `seo-meta-empty`, `page-dead-end`, `advantages-unconfirmed` and `text-measure`, which most stores of the sample trip; `rhythm-monotony` is info and fires on most homes of the sample, which is the sameness it reports. `adjacent-shelves` is an error on home and a warning elsewhere (a product page often puts two shelves together).

Some checks need the rendered page or the merchant, so they are review items, not rules: the «we do not deliver to this city» line under the buy button, advantage types against the store's real policies (the lint flags platform wording and template texts, `advantages-unconfirmed`, but not whether a confirmed text is true), technical names in the product chips, the contrast of `brandPrimary` against the button label, media reused from another store's template, and the city pop-up on phones. They are in [`checklist.md`](checklist.md) and [`product-page.md`](product-page.md#before-publishing-a-product-page).

## Rules

| Rule | Severity | Checks |
|---|---|---|
| `unknown-widget` | error | Widget is not a native Кит widget or YandexKit.Mystique. |
| `unknown-preset` | error | Preset is not in references/presets for this widget. |
| `unknown-role` | warn | Role is not in the plan vocabulary. |
| `chrome-count` | error | At most one of this widget per page. |
| `header-twice` | warn | Two Header sections on one page: check that only one renders. |
| `one-hero` | error | One hero per page (principle 3). |
| `hero-first` | warn | Home opens with a hero right under the header (principle 3). |
| `hero-slides` | warn | Hero with more than 3 slides; rotation is for multi-brand or promo retail only (principle 3). |
| `banner-mobile-image` | error | Every banner slide has its own mobile image (principle 4). |
| `banner-ratio-pair` | warn | Desktop and mobile banner ratios form a recommended pair, or a strip becomes a 4:3 card and a hero a full-height portrait (principle 4). |
| `button-text` | error | A slide with linkType button needs buttonText. |
| `adjacent-shelves` | error | No two same-shaped product shelves back to back (principle 7). |
| `adjacent-shelves-loose` | warn | On home, adjacent shelves that are not a bento pair read as one long list (principle 7). |
| `shelf-source-repeat` | error | One data source per shelf per page (principle 8). |
| `shelf-no-source` | error | A product shelf without dataSource renders empty. |
| `feed-relevant` | error | layout feed does not work with the relevant source. |
| `grid-count` | warn | In a grid, count is a multiple of columns. |
| `shelf-count` | warn | More than 4 product shelves on home fatigue the page (principle 22). |
| `category-entry` | warn | Home offers a category entry within the first two content sections (principle 9). |
| `tile-links` | warn | Navigation tiles lead somewhere (principle 10). |
| `banner-links` | warn | A banner that is not the hero leads somewhere (principle 10). |
| `subscribe-missing` | warn | Home closes with EmailSubscription (principle 15). |
| `subscribe-last` | warn | EmailSubscription is the last content section before the Footer (principle 15). |
| `subscribe-above-fold` | error | EmailSubscription never sits in the first two content sections. |
| `discount-without-promocode` | error | A subscription promises a discount only with a linked promocode (principle 23). |
| `consent-text` | error | The subscription consent text is never empty. |
| `home-length` | warn | Home has 8-14 content sections (principle 22). |
| `palette-missing` | error | A section palette id exists in the theme (principle 13). |
| `palette-churn` | warn | Body on the base palette; colour bands are few (principle 13). |
| `transparent-header` | warn | A transparent header needs an edge-to-edge BannersCarousel as the first section: over any other widget it lies on that section's background, and the image starts below it (principle 18). |
| `transparent-composite` | warn | A transparent header on the shared header makes every page transparent (principle 18). |
| `announcement-length` | warn | Each announcement module is 35 characters or less, title and body together (principle 20). |
| `faq-empty` | error | Every question has a question and an answer. |
| `faq-titled-twice` | warn | No two titled Faq sections on one page. |
| `image-sliver` | warn | No 10:1 or 7:1 image in an image-and-text split. |
| `zigzag` | warn | Stacked image-and-text blocks alternate sides (principle 16). |
| `placeholder-marker` | error | No <fill> marker or bracketed placeholder (a whole value in <…> or […]) is published (principle 23). |
| `platform-placeholder` | error | No visible platform placeholder text or all-zero phone is published (principle 23). |
| `media-missing` | error | No empty or data-URI image, no banner without slides or slide without an image is published (principle 23); on a custom section only a warning. |
| `custom-without-template` | error | A YandexKit.Mystique section always carries template_id. |
| `custom-share` | info | Native for commerce: more than 4 custom sections on home (principle 24). |
| `empty-enum` | info | hPadding and backgroundDark are not empty strings; the cabinet writes them in every store and the constructor tolerates them as existing drift. |
| `service-page-empty` | warn | A service page carries the merchant's text before it is linked (principle 23). |
| `shelf-source-id` | error | A shelf the plan adds carries dataSource.id: the schema requires it and `section add` refuses a shelf without one. |
| `listing-filters` | error (pulled: warn) | ProductsListing writes the filter position its settings version reads; left out, the storefront draws the inline filter sidebar nobody chose. A pulled or kept current listing on `inline` (the schema default every current listing carries) is reported as info. |
| `header-menu-empty` | error | The Header main menu is set where the settings version reads it: without it phones get no burger menu. A flat `modules.main.menu` in a header stored after version 1766500000000 is not read. |
| `header-burger` | info | On desktop the header shows a flat menu as links: a burger alone (menuVariant icon) hides the categories behind a click; it is for a nested or long menu. Info: the lint cannot see how deep the cabinet's menu is, and the burger is the sample's most common header. |
| `richtext-empty` | error | No RichText with neither a title nor content (an empty spacer). |
| `faq-repeated` | error | No question is asked twice in one Faq. |
| `faq-expand` | warn | Faq writes appearance.expand: the schema default opens the first answer; on a product page all answers start closed. |
| `product-card-missing` | error | A product page carries its product card: ProductDetails, or a custom card (role product) with its template. |
| `pdp-tail` | warn | On a product page the recently viewed shelf comes last: never the only shelf and never the first after the product card. |
| `shelf-buttons` | warn | All product shelves of a page show card buttons, or none: enableButton default lets the theme card decide. |
| `shelf-heading-orphan` | warn | No heading section right above a relevant or last_seen shelf: when the shelf is empty the heading stays alone. |
| `variant-dead` | info | An alternative product or category page has products or categories assigned in the cabinet (a page the workspace shows no counts for is reported to check). |
| `touch-stack` | warn | No tile grid of 3 or more tiles that stacks one tile per row on phones. |
| `touch-sliver` | warn | No 2-column tile carousel with one tile per phone view: the second tile shows as a sliver. |
| `shelf-touch` | warn | Product shelves show 2 cards per phone row; home product grids take 4 phone rows or fewer. |
| `touch-odd` | warn | A grid with 2 items per phone row holds an even number of items. |
| `touch-length` | warn | Home is 10 phone screens or shorter (estimate from the section settings). |
| `footer-untitled` | warn | Every footer menu column has a title: untitled columns print long flat lists on phones. |
| `button-text-hidden` | warn | A slide linked as a whole (linkType banner) carries no buttonText: the storefront never shows it. |
| `touch-inset` | warn | Text sections keep a phone side inset of 150 or more. |
| `banner-mobile-same` | warn | A slide without live text has a mobile image of its own, not the desktop file again. |
| `announcement-contacts` | warn | The bar carries no contacts: phones hide them. |
| `long-copy` | info | Image-with-text copy is 350 characters or less; centred RichText 200 or less. |
| `page-dead-end` | warn | A cart, 404, catalog or blog page leads to products: a shelf a first visitor sees (not recently viewed alone), or on 404 a linked banner. |
| `seo-meta-empty` | warn | A published static or custom page has its own seo_title and seo_description: empty, the title becomes a generic template and the description one that promises bonuses, instalments and cashback. |
| `page-h1` | warn | The page's H1 (the first titled section; a listing, card, cart or blog list first) is in the first two content sections and is not the subscription. |
| `advantages-unconfirmed` | warn | Product-card and cart advantages carry the merchant's own texts: the platform wording promises cash, pickup points and a courier; template slots promise another store's services. |
| `banner-live-text-fit` | warn | On a 7:3, 5:2 or 7:1 phone image the live title and subtitle together run about 5 words or fewer. |
| `story-dead-end` | warn | A story block on home leads further: a markdown link in its text (a custom story carries a button) (principle 16). |
| `story-measure` | warn | ImageWithText copy over 200 characters sits in a narrowed section of the theme's box (hPaddingDesktop 1400 or more, not across the screen), about 75 characters a line; a theme box over 1400 px has no padding that narrows it enough: shorter copy or `text-media-split` (principle 16). |
| `rhythm-monotony` | info | Home varies the recipe: 5 or more kinds of blocks, at least 2 content slots (not the bar, header or footer) off their first option or another theme, one signature block, a named look (principle 6). |
| `title-align-mixed` | warn | Section titles on a page share one alignment: a Faq follows only its own align, the listing H1 is always left (principle 1). |
| `text-measure` | warn | Long text runs 60-80 characters a line: RichText over 400 characters within about 800 px (7 columns of the default 1400 px box, 5 of a 1728-1920 px one), Faq questions within about 910 px (8 columns; 5-6 of a wider box), both in the theme's box rather than across the screen, an article aligned left (principle 25). The advice names what the section can write: fewer columns first, then a wider side padding; `presets apply --work` and a plan that names a text preset fit it to the theme's box. |
| `theme-font` | warn | The theme's text font is upright and plain: an italic or decorative face is the cabinet's to change (principle 2). |
| `hero-stack` | warn | No hero-sized banner (a hero preset, 2:1, 5:2 or full screen) right under the hero, a custom hero included (principle 3). |
| `banner-button-contrast` | warn | A button on a darkened slide (backgroundDark 24 or more) or under white text is primary: a secondary one reads as switched off. |
| `shelf-seeded-source` | warn | No shelf, tile or link points at the template's seeded «Категория 1-го уровня»: it shows the template's products or none. |
| `page-empty` | error | A published page carries a content section: a header over a footer is a dead page. |
| `system-widget-missing` | error | A system page (cart, search, blog, account, reviews and the like) carries the widget that makes it work. |
| `to-fill` | info | Values still to fill from the store or the merchant. |
