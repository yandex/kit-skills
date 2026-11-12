---
name: kit-storefront-design
description: |
  Design Яндекс.Кит storefront pages from native sections, desktop and phone. Picks a recipe
  by niche (fashion, beauty, catalog-led, single product, handmade, kids…) and by page
  type (home, product, category, catalog, search, cart, 404, about, contacts, service and legal
  pages, blog, FAQ, promo, loyalty) and a look; sets the theme first; merges schema-checked
  presets into sections; lints the plan against principles from 43 live stores (one hero, phone
  art per banner, no twin shelves, zero placeholders) and a phone check; hands execution to
  kit-storefront-constructor, and the brand's own blocks (signature block, story with a button)
  or a block no native widget can express to kit-custom-sections. Use for
  «сделай красивую главную», «собери главную», «оформи витрину», «какие секции поставить», «что
  не так с главной», "design my storefront homepage". Not for catalog, prices or orders
  (yandex-kit-cabinet) or markup (kit-custom-sections). Offline; store reads and writes go
  through the constructor (store token).
user-invocable: true
allowed-tools: Bash(scripts/design.py:*)
---

When editing this skill or its scripts, first read [`AGENTS.md`](AGENTS.md), which in turn points at the repository contract.

# Яндекс.Кит storefront design

**This skill decides what a page should look like; it writes nothing to the store.** It ships page recipes, partial settings (presets) for every native widget and for the theme, and a linter for page plans. `design.py` itself makes **no network calls and reads no token**; every read and write of the store goes through `kit-storefront-constructor`, which needs the store's API token. A custom section is `kit-custom-sections`. This page tells you which of their commands to run and when.

In every `kit.py` line below, `kit.py` is `scripts/kit.py` of `kit-storefront-constructor` (installed next to this skill) and `…` stands for the global flags that skill takes **before the subcommand**: `--skill-prompt "<the user's request>" --skill-session-id <one UUID per thread>`, plus `--skill-model` / `--skill-harness` when you know them, plus `--bff-url <b2c_url>` (step 2). Follow that skill's own rules for every call you make through it.

## Hard rules

1. **Native for commerce, custom for the brand.** Shelves, listings, the product card, the cart, category tiles and the subscription stay native: the merchant edits them in the cabinet and the platform keeps them working ([`references/native-vs-custom.md`](references/native-vs-custom.md)). The brand's own face is welcome as a custom section from `kit-custom-sections`: every home carries one signature block chosen by its look (a type band, a running line, a mosaic, real numbers, one review set large, reels), and the story is the `text-media-split` pattern with a button. 36 of the 43 reference homes are native-only, and that sameness is why stores built from the median look alike: native is the floor, not the ceiling.
2. **Never invent** ids (category, collection, product, page, section), media, palette ids, menu ids, promocodes, `settings.version`, prices, offer terms, delivery and return terms, legal text, FAQ answers or reviews. Take ids from the pulled workspace or, for a category or collection the workspace does not show, from **yandex-kit-cabinet**'s read-only lists; media from `kit.py … media upload`; texts from the merchant. When a value is missing, say which one and ask; never fill the gap with something plausible. The table of sources is in [`references/principles.md`](references/principles.md#never-invent).
3. **Theme first.** Most of a page's look — width, rhythm, gutters, heading scale, card style — lives in `global_settings`. Apply the recipe's theme presets before touching sections.
4. **Presets go through `design.py presets apply`**, over the pulled section (`--work … --section-id`) or over the constructor's skeleton for a new section. Never merge by hand, never write `settings.version` (`section add` stamps it, `section set` keeps it), never edit `work.json` by hand.
5. **Zero placeholders at publish.** Before any `content push`, `design.py plan lint --work work.json --page <alias> …` must report no errors on every page you changed; use `--all-pages` when you touched a page variant or a store-wide section. A `<fill: …>` or bracketed placeholder, visible platform placeholder text, an empty image, a banner slide without an image, an empty RichText spacer, a question asked twice, an empty main menu, or a subscription that promises a discount without a linked promocode is an error. Discounts promised in the bar and in banners are checked by hand ([`references/checklist.md`](references/checklist.md)). Warnings do not block: empty-string paddings and darkening that the cabinet itself writes are reported and tolerated, as the constructor tolerates them.
6. **Composite sections change every page.** Header, Footer and EmailSubscription are stored once and copied to every page: `section set` on them changes the whole store. The AnnouncementBar is one per store too (stores keep one, copied from home to the pages that show it): change the existing bar, never `section add` a second, and remember that `section remove` without `--page` takes every copy away (with `--page` it leaves one page, and `section status --page` hides it on one page). A store without a bar gets one in the cabinet constructor: `section add` makes an ordinary section of one page, not the copied one. A transparent header belongs only on a home-only header instance; the lint only warns about it on the shared header (`transparent-composite`) because live stores already run such headers, so never introduce one. `section add` of an EmailSubscription on a page without one makes an ordinary section, not a copy of the shared one; check it on the proposal preview.
7. **Publishing is the constructor's flow and the user's decision:** `content diff` → `content push --dry-run` → show the plan → explicit confirmation. For a new design prefer `content push --no-publish --confirm`, so the user reviews the proposal version before it goes live. Ask for that confirmation as for a publish: a server without proposal support publishes the edit anyway, and the constructor then exits `4`. On exit `4` the design is live: tell the user at once. A blanket «publish right away» is not a per-operation confirmation.
8. The menu, the logo, contacts and social links are cabinet data, not section settings. Change them in the cabinet; never guess a menu id and never empty the main menu. Emptying the header's secondary menu (`modules.additional.menu`) is a design choice with a cost on desktop: say so before doing it.
9. The theme font is changed in the cabinet theme editor: a font carries uploaded files per weight that cannot be looked up here, so never write `typography.fontFamily`.
10. **Design for the phone as well.** Native sections have no phone layout of their own: the phone page follows from the desktop settings. Budget home at about 10 phone screens (`plan lint` estimates it), give every banner re-composed phone art, keep product shelves 2 per phone row, and keep the product name and price on the first phone screen (a preview check, not a lint rule). See [`references/mobile.md`](references/mobile.md).
11. Answer in the language the user writes in.

## Workflow

**1. Brief.** Establish, in the user's words: what the store sells, how large the assortment is (a handful, dozens, hundreds), how products are photographed (on models, packshots on one backdrop, cut-outs), brand colours, which pages to design, and which facts the merchant can give (offer, delivery promise, promocode, tagline, FAQ answers). Ask for what is missing; do not assume.

**2. Read the store** (constructor):

`schema fetch`, and `section add` of a native widget (it stamps `settings.version` through the storefront), need the storefront address: the `b2c_url` that **yandex-kit-cabinet** reports (its own `store` command), taken as it comes, never built from the store name. It is a global flag, so it goes before the subcommand, as part of `…`: `kit.py --skill-prompt … --bff-url <b2c_url> schema fetch`. `KIT_BFF_BASE_URL` does the same where your environment keeps variables between commands.

```bash
kit.py … schema fetch
kit.py … content pull --out work.json
kit.py … sections list --page main
scripts/design.py plan lint --work work.json --page main     # what is wrong now
```

**3. Pick the recipe and the look.** Match the niche and the page type; `home-universal` when nothing fits, `home-other-niche` for a niche the sample lacks (it leans to fashion and beauty: food, pets, electronics, auto parts and service businesses are absent or too rare to count). Then pick a look in [`references/looks.md`](references/looks.md) (journal, gallery, dense catalog, playful, dark drop, premium) and name it in the plan with the 2-3 moves that set this store apart: the recipe is the sample's median, the look is what this store looks like. A recipe or preset marked *optional idea* rests on fewer than 3 stores of the sample: use it when the user asks for it or the look names it, never as a slot's first option. Product pages: [`references/product-page.md`](references/product-page.md); category, catalog and search: [`references/listing.md`](references/listing.md).

```bash
scripts/design.py recipes list --page-type home
scripts/design.py recipes show home-fashion
```

**4. Theme first.** Apply the recipe's theme presets over the pulled `global_settings`, then write the theme through the constructor. The report names the theme font (`font`); an italic or decorative text face goes to the owner for the cabinet. See [`references/theme.md`](references/theme.md).

```bash
scripts/design.py presets apply theme layout-editorial --work work.json --out gs.json
scripts/design.py presets apply theme card-lifestyle --base gs.json --out gs.json
kit.py … theme set --work work.json --settings-file gs.json
scripts/design.py theme palettes --work work.json      # which palettes the pages use; --prune --out gs.json drops the rest
```

**5. Plan the page.** Start from the recipe's plan skeleton, keep the steps that fit the store, and fill what is known: which existing section becomes which step, real data sources, real links. A slot with options comes as a `choose` list: pick one option per slot by the brief and the look and write its `widget` and `preset` (or `pattern`), keeping `slot`. Vary at least two content slots from their first option (the bar, header and footer do not count) and keep one signature block on home, an option of the recipe's `signature` slot; `plan lint` reports a plan that repeats its recipe (`rhythm-monotony`). Plan format: [`references/plans.md`](references/plans.md).

```bash
scripts/design.py recipes show home-fashion --plan > plan.json
```

**6. Lint the plan.** Fix every error; read every warning and either fix it or tell the user why the deviation is deliberate.

```bash
scripts/design.py plan lint plan.json
```

**7. Execute, section by section.** For an existing section, apply the preset over it; for a new one, over the skeleton. Fill the preset's `fill` items and the report's `open` items from the store and the merchant, then write through the constructor.

```bash
scripts/design.py presets show BannersCarousel hero-bleed-image       # settings, fill, module_example
# existing section
scripts/design.py presets apply FeaturedProducts home-shelf-carousel --work work.json --section-id <uuid> --out s.json
kit.py … section set --work work.json --id <uuid> --settings-file s.json
# new section
kit.py … schema widget --name YandexKit.BannersCarousel --skeleton > base.json
scripts/design.py presets apply BannersCarousel hero-bleed-image --base base.json --out s.json
kit.py … media upload --file ./hero-desktop.jpg --confirm > up.json    # after the user confirms the upload
scripts/design.py media-object --upload up.json --kind banner          # paste into modules[].slideMedia.media
kit.py … section add --work work.json --page main --widget YandexKit.BannersCarousel --settings-file s.json --position <n>
kit.py … section move --work work.json --id <uuid> --position <n>        # positions are 1-based and count the header
```

Positions are 1-based and count the header. `kit.py … sections list` reads the live store, so its numbers hold only until your first local edit; after that use the `position` each `section add` and `section move` prints. The lint numbers sections in display order, which usually but not always matches the stored layout. If the constructor refuses a preset result with `missing required property`, the store's schema asks for a key the presets do not know yet: take its value from `kit.py … schema widget --name <widget> --skeleton` into the settings file.

The header's `modules.main` is flat in some storefront builds and nested in others. `presets apply` keeps the shape the pulled header has; a header that shows none (the cabinet often stores it sparse) takes the shape of the live schema, which you pass with `--schema`. Header presets come in two kinds: an archetype (`centered-logo-links`, `centered-logo-burger`, `left-logo-inline-links`, `catalog-two-row`) sets `modules.main`, a phone-row preset (`header-touch-one-row`) only the secondary row; apply the archetype first, then the phone row over its result:

```bash
kit.py … schema widget --name YandexKit.Header --skeleton > header-schema.json
scripts/design.py presets apply Header left-logo-inline-links --work work.json --section-id <uuid> --schema header-schema.json --out h.json
scripts/design.py presets apply Header header-touch-one-row --base h.json --out s.json
```

The same `--schema` file of a widget supplies platform copy that a sparse pulled section rendered without storing it, such as the subscription's consent text. Where the store's skeleton shows it empty, ask the merchant for the text (with a link to the privacy page):

```bash
kit.py … schema widget --name YandexKit.EmailSubscription --skeleton > es-schema.json
scripts/design.py presets apply EmailSubscription band-two-columns --work work.json --section-id <uuid> --schema es-schema.json --out s.json
```

A product or category page variant (a product landing, a per-category hero): `page add` copies only the header and the footer, so the card or the listing, the shelves and the subscription are added one by one, each as an independent copy (the constructor cannot link a section to the default page yet). Once a store has variants, address those pages by `--page-id`; `--page productCard` is ambiguous. The sequence is in [`references/product-page.md`](references/product-page.md#product-landings-through-alternative-page-variants).

A video slide: upload the file, wait until it is ready, then build its media object.

```bash
kit.py … media video-upload --file ./hero.mp4 --confirm             # after the user confirms the upload
kit.py … media video-status --id <video_id> > status.json           # repeat until status is ready
scripts/design.py media-object --video-status status.json            # paste into slideMedia.media / mediaMobile
```

When a block needs a custom section (see rule 1), hand over to `kit-custom-sections`: `scripts/section.py patterns match "<need>"` there, then the `templates create` and `section add --widget YandexKit.Mystique --template-id …` commands it prints. If no pattern fits, say so; do not fake the block with a native widget that cannot carry it. **Create the templates first:** a new template reaches the workspace only with a fresh `content pull`, which starts from the store and drops every local edit not pushed yet. So build and `templates create` every custom section the plan names right after step 3, pull, and only then apply the theme and the sections; a template needed later means redoing the edits so far on the fresh pull (keep them as `presets apply` and `kit.py` commands), because a pull reads the live content: a `--no-publish` proposal is not in it.

**8. Review and publish.**

```bash
scripts/design.py plan lint --work work.json --page main --page 404 --page cart    # no errors
kit.py … content diff --work work.json
kit.py … content push --work work.json --commit-message "..." --dry-run
kit.py … content push --work work.json --commit-message "..." --no-publish --confirm   # after confirmation
kit.py … content push --work work.json --commit-message "..." --confirm   # publishes: only after the user approved the proposal
```

Walk the user through [`references/checklist.md`](references/checklist.md) on the proposal preview — desktop and phone — before the final push. There is no preview link: the proposal opens in the cabinet, in the storefront constructor's version history, by the number `--no-publish` prints. The H1, the meta tags and a product page's delivery line show only on the published storefront: check them right after the publish. On a product page preview the delivery line, the advantages and the chips: settings cannot show them.

Every published static page you designed (home, catalog, 404, service pages, blog, custom pages) gets its own SEO title and description from the store's facts: left empty, the storefront fills in a generic title and a description that promises bonuses, instalments and cashback (`seo-meta-empty`). Write them with `kit.py … page set --work work.json --id <page id> --seo-title … --seo-description …` before the push. Cart, search, favourites and account pages show the cabinet's store-wide template, and product and category pages their own cabinet fields. The page's H1 is the first titled section, never a banner (`plan lint` prints it): give the section under the hero a title that says what the store sells rather than relying on `--seo-h1`, which home and custom pages do not print.

## Commands

```bash
scripts/design.py doctor                                    # counts and consistency of the references
scripts/design.py recipes list [--page-type T] [--json]
scripts/design.py recipes show ID [--plan [--with-optional]] [--json]
scripts/design.py presets list [WIDGET] [--json]
scripts/design.py presets show WIDGET [PRESET]              # WIDGET: Header, YandexKit.Faq, theme, ...
scripts/design.py presets apply WIDGET PRESET (--base FILE | --work FILE [--section-id UUID]) [--schema SKELETON [--as-new]] [--restyle-modules] --out FILE
scripts/design.py plan lint PLAN.json [--final] [--strict] [--json]
scripts/design.py plan lint --work work.json (--page ALIAS_OR_ID ... [--page-type T] | --all-pages) [--strict] [--json]
scripts/design.py plan rules
scripts/design.py theme palettes --work work.json [--prune --out gs.json] [--json]   # palettes in use; drop the unused
scripts/design.py media-object (--upload up.json [--kind image|banner] | --video-status status.json)
```

`presets apply` reads `--base` as the output of `kit.py schema widget --skeleton`, of `kit.py section show`, or as plain settings. Plain settings carry no version, so they are written as for a new section: a listing's filter position in the shape of the `--schema` skeleton, else their own, else the current `filters`. Plain listing settings that show the root `filtersPosition` keep it (they may be an earlier `--out` of a stored section without a version, which `section set` keeps unversioned), and `open` says that a new section needs `--as-new` instead. For a stored section pass it with its version (`--work` with `--section-id`, or `section show`). A theme preset over a base without a `version` says so in `adapted`: apply theme presets over the pulled `global_settings` only. It keeps the section's layout: header presets land in the flat or nested `modules.main` layout the section already has, a header that shows neither takes the live schema's (`--schema`, the output of `kit.py … schema widget --name <widget> --skeleton`; without it the command stops and asks for it), a banner that stores the older single `aspectRatio` gets the desktop ratio there, and the keys whose place depends on the settings version land where the stored version reads them: a listing's filter position (`filters.position`, or the root `filtersPosition` of an older section) and the theme's card order (`order`, or `elementsOrder` of a newer theme). `presets show Header <preset>` prints both shapes. Widgets marked *leave default* (gift card, favourites, bundle, reviews, unsubscribe, discount ladder) have no preset: `presets show <Widget>` says why. Pulled sections are often sparse (`{"section": {}}`): an object the preset creates in one gets the keys the schema requires, with schema defaults, with the platform copy the `--schema` skeleton shows (a consent text), or with a `<fill: …>` marker where the value is the store's or the merchant's (a menu id; a consent text the skeleton shows empty). An empty-string `backgroundDark` becomes `0`, which renders the same. A preset can also take keys away (its `unset` list, shown by `presets show`): `hero-contained-card` removes the `"0"` side paddings of the edge-to-edge hero it replaces, so the card takes the theme's. `--as-new` turns a pulled section into settings for a new one on another page (a variant's card or shelf): the pulled settings over the `--schema` skeleton, without `version` and the cabinet's empty-string paddings; a shelf without `dataSource.id` gets the type as its id for `relevant` and `last_seen`, and a marker in `open` for a category or collection. A preset's `module_style` restyles empty modules; slides and tiles that already carry content keep the style keys they have, and `adapted` lists every kept value (`--restyle-modules` overwrites them). The report lists `fill` (what the preset cannot supply) and `open` (what is still empty or a placeholder in the result).

## Reference map

| Topic | File |
|---|---|
| The 25 principles, with evidence and the lint rule behind each | [`references/principles.md`](references/principles.md) |
| Phones: what the platform does, principles, the screen budget, the phone check | [`references/mobile.md`](references/mobile.md) |
| Product page: what renders, recipes, tail rules, checks before publishing, product landings | [`references/product-page.md`](references/product-page.md) |
| Category, catalog and search pages | [`references/listing.md`](references/listing.md) |
| Theme: platform defaults to change, rhythm, palettes, niche to theme | [`references/theme.md`](references/theme.md) |
| Looks: six directions taken from the stores that stand out, with theme, header, hero and signature block | [`references/looks.md`](references/looks.md) |
| Recipes by niche and page type | [`references/recipes/INDEX.md`](references/recipes/INDEX.md) (bodies: `recipes show`) |
| Presets per widget | [`references/presets/INDEX.md`](references/presets/INDEX.md) (bodies: `presets show`) |
| Native or custom | [`references/native-vs-custom.md`](references/native-vs-custom.md) |
| Plan format and lint rules | [`references/plans.md`](references/plans.md) |
| Pre-publish review | [`references/checklist.md`](references/checklist.md) |

Read the JSON under `references/recipes/` and `references/presets/` through `design.py`, not by loading the files into context.

## Exit codes

- `0` ok (warnings allowed); `2` the plan has errors — or warnings under `--strict` — or the shipped references are inconsistent (`doctor`); `3` usage; `5` recipe, preset or page not found.
- `1` and `4` belong to the constructor's scale and never come from `design.py`, which has no network. From `kit.py content push --no-publish`, `4` means the edit was published (rule 7).

## Scope boundaries

- Reading and writing pages, sections, the theme and media; schemas and skeletons; publishing — **kit-storefront-constructor**.
- Custom section markup (html + css + json_schema) — **kit-custom-sections**.
- Catalog, collections, prices, promocodes, orders — **yandex-kit-cabinet**. A promocode id for the subscription form comes from there, and so do the ids and slugs of categories and collections that a new tile, banner link or shelf points at, and the check that a linked product exists (its read-only `list /v1/categories`, `list /v1/collections` and `list /v1/products`).
- Store health (unbuyable products, stuck orders) — **kit-store-checkup**.
- This skill makes no HTTP request and reads neither `KIT_TOKEN`, `KIT_TOKEN_FILE` nor `~/.yandex-kit-skills/kit_api.token`.
