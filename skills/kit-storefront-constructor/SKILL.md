---
name: kit-storefront-constructor
description: |
  Manage a Яндекс.Кит storefront through compact Experimental API projections: read pages, sections, versions, and section-template schemas; inspect native-widget schemas from the storefront BFF and build valid settings skeletons; edit, hide, or remove sections, page layouts, and global settings in a local write workspace; create, retitle, hide, or remove storefront pages with server-mirroring local validation; publish the result through a confirmed full-replace write with local integrity gates — or store it as an unpublished proposal version (no-publish preview) that leaves the storefront untouched; create validated custom section templates; or explicitly upload local storefront images, videos, and brand fonts. Also sets up the store API token: locally through a one-shot page on 127.0.0.1 the user types it into instead of pasting it into the chat, and in a hosted session through that session's environment settings or the token sent in the conversation.
user-invocable: true
allowed-tools: Bash(scripts/kit.py:*)
---

When editing this skill or its scripts, first read [`AGENTS.md`](AGENTS.md), which in turn points at the repository contract.

# Яндекс.Кит storefront constructor

Use the fixed-route, Python stdlib client in `scripts/kit.py`. It downloads endpoint responses internally but returns compact local projections by default. Full storefront payloads (~660 KB live) live in a local workspace on disk — never in your context.

## Hard rules

1. Use only routes exposed by `scripts/kit.py`; do not use generic HTTP clients, other routes or write methods, UI scraping, or browser automation.
2. Two backends, two URLs. Core experimental API: `KIT_API_BASE_URL` / `--base-url`, store-scoped Bearer token. Storefront BFF (schema + migrate, no auth): `KIT_BFF_BASE_URL` / `--bff-url`, set to the store's own storefront origin, which serves `/constructor-api`. The client derives it only when the Core API shares that host; otherwise it refuses. **Get the origin from the store itself** — this skill has no command for it: the `yandex-kit-cabinet` skill's `scripts/kit.py store` reports it as `b2c_url`; without that skill, ask the user for the address their customers open. Never guess it and never assemble it from the store slug, because a store on its own domain has a different `b2c_url`.
3. Resolve the token through `KIT_TOKEN`, then `KIT_TOKEN_FILE`, then `~/.yandex-kit-skills/kit_api.token`. Never print, log, commit, prompt with, or pass it as an argument.
4. Prefer compact commands. Use `--raw` and `versions list --all` only for a deliberate high-volume diagnostic.
5. Select pages by exact `alias` or UUID `id`; sections and templates by UUID only. Never infer identity.
6. Every mutation — template create, media and font upload, and `content push` — requires explicit per-operation `--confirm`. Dry runs and unconfirmed mutations perform no token loading and no network request.
7. The only write transport is the full-replace `PUT /constructor/content` behind `content push`. There is no partial write; the push gates run locally before any confirmation, and a failed gate means the workspace is wrong — never bypass a gate by hand-editing.
8. Never edit `work.json` by hand. All edits go through `page add/set/remove`, `section set/add/remove/move/status` and `theme set`, which validate settings against the authoritative schema at edit time and keep composite copies in sync.
9. `migrate` is called by the client with strictly empty settings, only to learn a widget's actual version. Never send real settings to migrate: on live data it overwrites content with placeholders.
10. Preserve the write safety invariants: read-before-write (`content pull`), CAS through `base_version_id`, one reread on 409, and no automatic retry of any mutation.
11. Template update/delete, media deletion, and mass-destructive operations are unavailable. Refuse them without an API call and explain that the operation is «не доступна».
12. Upload only a local file path supplied by the user. Never fetch source media from a URL. Images support non-SVG/non-ICO `image/*`; videos support mp4, mov, webm, avi, and flv; fonts support .woff2, .woff, .ttf and .otf, and only a font the user confirms they hold a web licence for; the maximum is 104857600 bytes.
13. On every `kit.py` call that can reach the API, pass the user's prompt that caused the skill invocation via `--skill-prompt` and a UUID via `--skill-session-id`. Generate one session UUID per Claude/Codex thread and reuse it for every call in that thread — including calls to the other Кит skills — and never in another thread. Put both flags before the subcommand. The client truncates the prompt to 1000 characters and sends it Base64-encoded, as `X-Skill-Prompt` and `X-Skill-Session`; `X-Skill` it sets by itself, on API and storefront requests alike. Omitting the flags fails silently — the call still succeeds, the request is simply unattributed — so this is a rule, not a client-side check. Also pass `--skill-model` (the model you are, e.g. `claude-opus-5`) and `--skill-harness` (the agent you run in, e.g. `claude-code`, `codex`) when you know them: both are optional, and an unknown one is better left out than guessed. The OS the client reads for itself. Only `X-Skill` goes to the storefront — everything else stays on the authenticated API.
14. Keep execution Python 3 stdlib-only. The client sends no events anywhere; the identification headers above are the only telemetry, and `YANDEX_KIT_SKILLS_TELEMETRY_DISABLED=1` drops all of them. Pass the flags regardless — honouring the opt-out is the client's job, not yours.
15. The client keeps itself up to date on its own. The API names the current skill versions on every response, and when this copy is older the client reinstalls it from `github.com/yandex/kit-skills` after the command has finished. You will see one line on stderr starting with `kit-skills:`. If it reports an update, **tell the user the skill was updated and that the agent has to be restarted** — the instructions you are reading now came from the previous version, so a command that changed shape in the new one would be called wrong. If it reports a failure, mention it once and carry on: the command you ran is unaffected, and the update is retried against the next version, not this one. Never invoke the update by hand and never work around it with `git`, `pip`, or a manual download. `YANDEX_KIT_SKILLS_AUTOUPDATE_DISABLED=1` turns the reinstall off; if the user asks not to be updated, that is the answer — the client then only reports that a newer version exists.

## First run

For a user with no token yet, the setup *is* the conversation. Before asking for
anything, say in one short message what this skill can now do for their store —
pages, sections, images and video, publishing after their confirmation — three or
four things in their own words, not a command list. Then move to the token as the
one step left, taking the route the section below picks for where you run —
`token web` on the user's own machine, the session's environment settings or the
token sent in the conversation when you are hosted. A bare credential request as
an opening line is the difference between a tool that arrived and a tool that is
theirs.

Say once, while setting the token up, what the client sends with its API requests:
the skill name and the user's prompt, up to 1000 characters, to the Кит API only,
and that `YANDEX_KIT_SKILLS_TELEMETRY_DISABLED=1` turns it off. Once, at setup —
not in every session, and not buried after the work has started.

Answer in the language the user writes in.

## Token and contour setup

```bash
scripts/kit.py env
scripts/kit.py token status
scripts/kit.py token save
scripts/kit.py token web [--port N] [--timeout SECONDS] [--no-open] [--force]
```

`env` prints the resolved Core URL, token source, BFF URL, and schema-cache state — never the token.

`token web` is how a user hands over a token in a chat session: it opens a one-shot page on
`127.0.0.1` (a free port by default), the user types the token into the form, and the browser
posts it straight to `~/.yandex-kit-skills/kit_api.token` (mode 600). The link carries a one-time
secret; the page loads no external resource, serves one successful save, then the server exits.
Nothing about it touches the cabinet — it is not the browser automation rule 1 forbids.

When the token is missing, prefer this over asking for the value. The command blocks until the save
or the timeout (default 300 s), so run it in the background, tell the user the printed URL, and poll
`token status` — exit `0` means saved, `2` means timed out or refused. `token save` stays for a
real terminal (hidden prompt) or a pipe. **Never pass a token on a command line** — that rule has no
exceptions on any surface.

**The page helps only when this client runs on the user's own machine.** In a hosted session — a
container, a cloud sandbox, an SSH shell — `127.0.0.1` is *that* host, and the link resolves to the
user's machine, where nothing listens. The command detects this and exits `2` before opening a
socket rather than printing a dead link; never bind anything but loopback to work around it.
`--force` exists only for the case where the user already forwards this host's port to their
browser (`ssh -L`).

There the token comes another way, and the order matters — take the first one the user can actually
do, do not stop at the first one they cannot:

1. **`KIT_TOKEN` / `KIT_TOKEN_FILE` in that session's environment or secret settings.** The value
   never enters the conversation. Offer this first and walk them through where the setting lives.
2. **The user sends the token in the conversation — text or file — and you save it with
   `token save` from stdin.** The route for a hosted session where those settings are out of
   reach. Take it without hesitation: the two differ in one thing only, that this one leaves the
   value in the conversation history. Say that before they send it, and once saved tell them they
   can revoke the token in the cabinet under «Настройки → API» when the work is done.

A merchant who is not a developer is the expected user of this skill: give the click path, not the
concept, and never leave them without a next step.

## Compact read commands

```bash
scripts/kit.py content summary
scripts/kit.py pages list
scripts/kit.py page show --alias main
scripts/kit.py sections list --page main
scripts/kit.py section show --id <section-uuid>
scripts/kit.py versions list [--limit 5] [--since 2026-08-01T00:00:00Z]
scripts/kit.py versions content --id 42
scripts/kit.py templates list
scripts/kit.py templates show --id UUID
scripts/kit.py templates versions --id UUID [--version-id UUID]
scripts/kit.py templates export --id UUID --out DIR
scripts/kit.py content preview --template-id UUID
```

`sections list` returns section UUIDs — the reference for any edit. `section show` returns one section's settings, its schema source, and **every page copy**: a composite section (header/footer) is stored once and copied to all pages; the backend keeps the parent copy's settings.

`page show`, `sections list`, and `section show` report each section's `status`. `hidden` means the section is stored with its settings but **not shown on the storefront** — never describe such a section as visible. `null` means the response carried no status, not that the section is live. Hide or show a section here with `section status --id X --status hidden|published` — every copy, or with `--page`/`--page-id` only the copy on one page (status is stored per copy). The server accepts `hidden` only for `AnnouncementBar`, `BannersCarousel`, `EmailSubscription`, `Faq`, `FeaturedBlogs`, `FeaturedCollections`, `FeaturedProducts`, `ImageWithText`, `LoyaltyProgram`, `Mystique` and `RichText`; for any other widget the command refuses locally (the server would answer the whole write with 400) — the built-in `Header` and `Footer` included, which are not removed from a single page either; a custom header or footer template in the slot (`section set --widget`) can be hidden — and `content push` refuses a section of such a widget that came hidden from the server: the server checks every section of a write, so it blocks every write until it is shown or removed. Other edits keep the status a section already has; a section added here is `published`.

`templates export` writes `template.html`, `template.css`, `template.schema.json`, `template.meta.json`, and `template.js` with its `script`. `template.js` is written always — the server sends `script` with every template — and 0 bytes means the template has no script; the output's `script_bytes` says so without opening the file. Carry that script into the `templates create` file of a copy; leaving it out creates the copy without its JS.

`section set --template-id NEW` moves a published custom section onto another template, on every page it appears on, and checks the settings against the template it moves to. This is how a custom section's markup is changed, because the live template cannot be edited in place. Settings stay mandatory: state what the fields hold under the new template instead of letting the old values land in a shape that may have no room for them. A template created after the last pull is not in the workspace snapshot yet: add it with `content templates-sync --work work.json` (or create it with `templates create --work work.json --confirm`, which adds it right away). Both keep every edit already in the workspace — do **not** pull again for this, a new pull throws those edits away. A template the snapshot knows but the server has changed is reported, never replaced.

## Native widget schemas (BFF)

```bash
scripts/kit.py schema fetch                          # cache by ETag; other schema commands work offline
scripts/kit.py schema widgets
scripts/kit.py schema widget --name YandexKit.Faq
scripts/kit.py schema widget --name YandexKit.Faq --skeleton
scripts/kit.py schema theme
scripts/kit.py schema widget-version --name YandexKit.Faq
```

Run `schema fetch` once per session before editing native sections. `--skeleton` builds a locally validated settings object from schema defaults — the ones the cabinet editor uses, including defaults written next to a `$ref` — the correct starting point for a new native section. `widget-version` reads the widget's actual `settings.version` through migrate with strictly empty settings; widgets without migrations have none.

## Editing the storefront (write workspace)

```bash
scripts/kit.py content pull --out work.json
scripts/kit.py content templates-sync --work work.json    # templates created after the pull
scripts/kit.py page add       --work work.json --title "Акции" --pattern /promo [--alias A] [--status hidden] [--seo-title ...] [--no-chrome] [--copy-layout-from default]
scripts/kit.py page set       --work work.json --id UUID [--title T] [--pattern P] [--status S] [--seo-title ...] [--clear-meta]
scripts/kit.py page remove    --work work.json --id UUID
scripts/kit.py section set    --work work.json --id UUID --settings-file s.json
scripts/kit.py section set    --work work.json --id SLOT --widget YandexKit.Mystique|YandexKit.Header|YandexKit.Footer [--template-id UUID] --settings-file s.json
scripts/kit.py section add    --work work.json --page main --widget YandexKit.Faq --settings-file s.json [--position N]
scripts/kit.py section add    --work work.json --page main --widget YandexKit.Mystique --template-id UUID --settings-file s.json
scripts/kit.py section remove --work work.json --id UUID [--page alias | --page-id UUID]
scripts/kit.py section status --work work.json --id UUID --status hidden|published [--page alias | --page-id UUID]
scripts/kit.py section move   --work work.json --id UUID --position N [--page alias]
scripts/kit.py theme set      --work work.json --settings-file gs.json
scripts/kit.py content diff   --work work.json
scripts/kit.py content push   --work work.json --commit-message "..." --dry-run
scripts/kit.py content push   --work work.json --commit-message "..." --confirm
scripts/kit.py content push   --work work.json --commit-message "..." --no-publish --confirm
```

The flow is always: **pull → edit through commands → diff → dry-run → human confirmation → push --confirm**. Without `--out`, `pull` writes to the per-store workspace under `~/.yandex-kit-skills/workspace/` (override: `KIT_WORKSPACE_DIR`). Pull with the store's storefront address set (`--bff-url` or `KIT_BFF_BASE_URL`): the manifest records it as `bff_url` with the matching `schema_build_version`, and later workspace commands check native widgets against that storefront's schema without repeating the flag — a flag or `KIT_BFF_BASE_URL` naming another host is refused, not obeyed. A pull without it records `bff_url: null` and a `schema_note`; native-widget edits then ask for the address rather than borrow a schema cached for another store, and so do custom-section settings that use an editor model (`ImageModel`, `LinkSource`, `Palette`) the template takes from the storefront schema — the templates `kit-custom-sections` builds do. Run `schema fetch` for the store's address and pass the same `--bff-url` to that command: a workspace without a recorded address takes it.

- `section set` rewrites **all copies** of a composite section at once and preserves `settings.version` of an existing section — changing a version is a migration and is not supported.
- `section remove` without a page removes every copy. With `--page`/`--page-id` it takes the section off that one page and leaves the other copies — the way to drop a block that a page only repeats (the main page's FAQ on the catalog or a product page). When the parent copy goes, the first remaining copy becomes the parent; a single remaining copy becomes an ordinary section. The header and footer slots are never removed from one page.
- `section add` for a native widget stamps `settings.version` from `schema widget-version` automatically; widgets without migrations get no version field.
- `section add` without `--position` puts the section at the end of the content, right before the footer slot. `section add` and `section move` take a free sequence number between the neighbours; when a page has none left, only the sections between the header and footer slots are renumbered, in steps of ten. The header keeps its number and is not moved. A footer that sits right after the content (many pages number everything 1..N, footer included) moves up to 9999 on that page to make room — the report says so under `footer_renumbered`; the storefront shows the footer below the content whatever its number, so nothing visible changes.
- Edit-time validation ignores schema drift that already exists in the pulled data — in a section copied by `page add --copy-layout-from default` too; only errors your edit introduces are blocked.
- `content push` runs local gates before anything else: the base manifest exists, no page is lost (a default page with an alias can never be dropped; other removals need `--allow-page-removal`), no alias changed, added/changed widgets exist, changed settings validate, composite copies are in sync, no section is hidden that its widget does not allow. `--dry-run` and refusal happen completely offline.
- The push plan shows the diff summary, never the full payload, plus `warnings` when there are any. Page edits are part of it: `pages_changed` lists every kept page whose title, pattern, status or SEO meta changes, with the value before and after; a changed address also adds a warning that the old one stops opening. Show it to the user, warnings included, and get explicit confirmation before `--confirm`.
- On 409 the client rereads once, reports `version_conflict`, and stops. Re-pull, reapply, and ask for fresh confirmation — never retry automatically.
- The success response is compact: new version, `pages_count`, `sections_count`. The client compares them with local expectations and warns loudly on divergence. A confirmed push prints exactly one JSON document on stdout, `{"plan": …, "result": …}`; warnings and errors go to stderr.
- After every successful push the client rebases the workspace onto the version the server reports as active (`active_version_id`), so the next push carries a fresh CAS base instead of hitting 409.

### Pages: what a human chooses and what is derived

A page has three human-owned dimensions — **title**, **pattern** (URL route; free for custom pages, fixed for aliased system pages), and **status** (`published`/`hidden`; `hidden` is honored only for custom pages and a fixed subset of aliases). Static pages (pattern without `:param`) may also carry SEO meta. Everything else is derived and never set by hand: `exact` follows the pattern, `variant_type` is recomputed by the server, and the page `id` is generated. Several pages per alias exist only for `productCard` and `categoryAndCollection` — those alternatives are the «page templates» merchants assign to individual products and collections in the B2B cabinet; the assignment itself is a separate entity outside this skill.

- `page add` derives everything derivable and checks the rules the server refuses a write for (known alias, uniqueness, one default per alias, hidden subset, meta-on-static, allowed address characters). It refuses a custom page on a system page's whole pattern, because the server never keeps one there: when the store has that system page (`/cart` — every store is created with it), the server refuses the whole write with 400 (two pages on one pattern); when it does not (`/account/refunds` in a store without the refunds page), the server silently turns the custom page into that system page. By default `page add` copies the store header and footer onto the new page — the store's own custom header or footer included (`--no-chrome` to skip; `cartPreview`, `unsubscribe`, `emailConfirmed` and `emailConfirmationFailed` never get them, as on the server). A system page (`--alias`) also gets its content widget the way the server creates one — the refunds list on `accountRefunds`, the product block on `productCard`, the listing on `categoryAndCollection`, a `RichText` headed by the page title on the text pages — built from the widget schema skeleton with the widget's version and listed in `content_widgets_added`. That needs the storefront address and `schema fetch`; without them the page is added empty between header and footer, with a warning to pass on. A new `productCard` / `categoryAndCollection` template can instead start from the default template's current layout with `--copy-layout-from default`: its sections become new sections, still carrying the default template's settings byte for byte — the product block or the listing too, even when the server stores the default one as a composite (alone, or shared with the search page), so the new template's block is its own. Only a block the default template shares with other pages (a subscription, an announcement bar, a FAQ copied from the main page) stays that one shared section: it is listed in `shared_sections` with the pages in `also_on`, not in `content_widgets_added`, and `section set` on it changes all of them. What today's widget schema flags in them is listed as `inherited_drift` and does not block the push — the gate judges each copy against the section it came from, so an edit of a copy is checked only for what it changes. New pages exist only in the workspace until a confirmed push; select them in section commands by `--page-id`. A new custom page is linked from nowhere — no menu points at it, and menus are not editable here: the report's `reachability_note` and a plan warning say so. Link it from a section in the same edit (a button or a tile), or have the user add a menu item in the cabinet right after the push.
- One rule is the client's own: a custom page may not take an address a system page of this store already serves — `/account` (the profile page answers there too) or `/catalog/sale` (matched by `/catalog/:categorySlug`). The server would store such a page, but the storefront would then open either of the two at that address. When the store does not have that system page, the address is accepted with a warning, since the page may be added later (almost every system page is created with the store and a default one cannot be removed, so this is mostly a store older than that page); adding a system page whose route matches a stored custom page warns the same way. `page add --alias` does not create `emailConfirmed` and `emailConfirmationFailed`: their content widgets are not in the storefront schema, so the page could only be empty.
- A custom page address is one or more `/part` segments of Cyrillic and Latin letters, digits, spaces and `. _ ! ' + -`. A newer server rule checks this on **every** custom page of each write. An address the edit introduces is refused locally. A stored page whose address breaks the rule only adds an entry to `warnings` in the push plan, because a backend without the rule accepts the write and one with it answers 400 without writing anything. Relay that warning before asking for confirmation. If the push is refused for that address, the way out is `page set --pattern`, after telling the user that links to the old address stop working.
- `page set` changes title, status, SEO meta, and — for custom pages only — the pattern. Aliases and variant types are immutable, and so is the pattern of an aliased page: it belongs to the server and goes out exactly as it was read, because an older backend refuses any other pattern and a newer one writes its own whatever is sent.
- The storefront never reads the page's own SEO title, H1 and description on `cart`, `cartPreview`, `search`, `favouriteProducts`, `unsubscribe`, `emailConfirmed`, `emailConfirmationFailed` and the account pages (`accountProfile`, `accountOrders`, `accountNewsletters`, `accountRefunds`): it shows the store-wide template from the SEO settings. `--seo-*` on such a page is stored and has no visible effect — the report and the push plan carry a warning; pass it on instead of saying the SEO changed.
- On every other static page the storefront reads the SEO title and description but **never prints `seo_h1`**; `--seo-h1` is stored and changes nothing visible, and the report and the plan say so. The H1 is the first **section title** on the page that is not a link. A `BannersCarousel` never takes it — its slide titles are h2; a product shelf on a category or collection does not either — its title links there; a custom section takes part through `ya-kit-section-title` without `to`. On product, listing, blog, cart and account pages the page's own block takes it first. That rank is page-wide: a loyalty-programme block, or a blog listing without a title, outranks section titles without printing a heading of its own, so the server HTML of that page has no H1 (`h1_hint.source` `none_in_server_html`, the block in `raised_by`; the browser promotes the section title named in `after_hydration` only once the scripts run) — give the listing a title, or keep the loyalty block on a page of its own. To set the H1, give the first titled section the intended title. `page show` names the section as `h1_hint` (by the same rule, from settings and templates; `text` is `null` when it comes from data) and lists under `also_h1` custom sections whose markup writes an h1 of its own — each is one more H1 on the page.
- `page remove` refuses default system pages outright (the server 400s the whole write) and for everything else spells out exactly what disappears: the page and its last-copy sections. The push still demands `--allow-page-removal` plus explicit confirmation; remember that a page without an alias would otherwise be dropped **silently** by any full replace. A page a menu item links to cannot be removed at all: the server refuses the whole write with 400, names the page as linked to a menu item and writes nothing. This skill cannot see the menu, so say before the push that this may happen; if it does, the user removes that item in the cabinet (section «Меню») and the same push is repeated — a menu edit does not move the content version. Say before that menu edit that it is live at once: menus are not versioned, so the removed item disappears from the storefront immediately, and neither `--no-publish` nor a version revert brings it back. Removing an `alternative` `productCard` or `categoryAndCollection` page (a template assigned to products or collections) clears those assignments: the items fall back to the default template, the report carries `assignment_warning` and the push plan repeats it. Reassigning is done only in the cabinet.

### Write without publishing (`--no-publish`)

`content push --no-publish --confirm` sends the same full-replace body with `activate: false`. In **one server transaction** the edit is stored as a proposal version and a copy of the previously active content is re-activated — the storefront never shows the proposal, not even for a moment. The response carries both numbers: `version` (the proposal) and `active_version_id` (what is live now).

- Report **both** numbers to the user and **never** say the change is applied or visible. The correct wording names the proposal version and the version currently on the storefront.
- **You cannot render the proposal.** No route of this API previews a stored version (`content preview` works only with a store theme). The visual check — desktop and phone — is the user's: the proposal opens in the cabinet's constructor history by its number. What you can check offline is the snapshot itself: `versions content --id N` and the design lint run on it. Publishing to look and then writing the previous content back is a different operation, done only with the user's explicit consent.
- The workspace manifest moves to `active_version_id`; the working copy keeps the proposal, so a follow-up confirmed `content push` (without `--no-publish`) publishes exactly that proposal.
- **Old-server guard.** A server without this rollout silently ignores `activate` and **publishes the edit**. The only reliable signal is the response: if it carries no `active_version_id`, the client reports «правка опубликована, режим предпросмотра не поддержан этим стендом» and exits `4`. Exit 4 means the change is LIVE on the storefront right now — tell the user immediately and never present it as a preview or retry silently.

### What the no-publish mode can and cannot roll back

Everything that lives in the content version rolls back honestly: section settings, section composition, `template_id` values, global settings. **Global section templates do not**: the write transport does not carry them, and reading any version returns the *current* templates. Editing a live template changes the storefront immediately and in every version — reverting a version does not undo it. Therefore:

- To preview a markup (html/css/schema) change of a custom section: create a **new** template with `templates create`, then switch the section's `template_id` to it in the proposal. Never modify a live template for a preview. An unreferenced template renders nowhere, so creating one is harmless.
- The push gate enforces the boundary: a workspace whose `section_templates` differ from the base snapshot is refused locally — such a change could neither be written nor previewed through push.
- **Product and collection template assignments do not roll back either.** A write that removes an `alternative` `productCard` or `categoryAndCollection` page clears every assignment of products, categories or collections to it — the proposal write of `--no-publish` included, and a reverted version brings the page back unassigned. Such a removal is never a safe preview; the plan says so.
- **The menu is outside versions.** A menu item removed in the cabinet (for instance, to unblock a page removal) disappears from the live storefront at once; nothing here restores it. Warn the user before that menu edit, not after.

## Create a custom template

Prepare one UTF-8 JSON object with non-empty string fields `title`, `html`, `css`, and `commit_message`; `json_schema` must be a string containing valid JSON. `script` is optional: a string of the template's JavaScript, sent as `""` when absent. The storefront runs it only for a store on its own domain, not on the default Кит address; this skill passes it through and never writes it. `alias` is optional and normally left out — see below.

```bash
scripts/kit.py templates create --file section.json --dry-run
scripts/kit.py templates create --file section.json --confirm [--work work.json]
scripts/kit.py templates list
```

The dry run prints a compact plan, not the source: `title`, `alias`, the byte sizes of html, css, schema and script, `body_sha256` (over the body with sorted keys — the same in the dry run and the confirmed run), and `schema_fields` (path, type, title of every field the section gets), plus `script_warning` when the template carries JS. `--show-body` adds the exact body for a deliberate look at the source. Always show the dry-run plan before asking for confirmation. With `--confirm` the client prints one JSON document, `{"plan": …, "result": …}`, where `result` is the created record without its source. Creating a template does **not** attach it to any page: attach it afterwards with `section add --widget YandexKit.Mystique --template-id` plus `content push`. There is no DELETE route; record every live-created ID for manual cleanup through the B2B cabinet.

`alias` is not a name and not an identity — select templates by UUID only. The server stores any string without checking it. What reads it: the store-wide header and footer logic on the server, the storefront — which renders every section on a template aliased `YandexKit.Header` or `YandexKit.AnnouncementBar` in the header area and on `YandexKit.Footer` in the footer area, outside the page content — and the cabinet's «Заменить» list. Whether a custom section can be hidden no longer depends on it. So the client handles alias like this:

- none: a regular section. Templates made in the cabinet have none too;
- `""`: accepted and **dropped** from the body, so the template is stored without an alias, the way the cabinet stores its own. The server would keep the empty string as a set alias that names nothing;
- `YandexKit.Header` / `YandexKit.Footer`: a store-wide header or footer, set by `--chrome-slot` (next section);
- anything else: refused by this skill. An alias naming a native widget is how the cabinet offers a template under «Заменить» for that widget, and this skill does not support that; `YandexKit.AnnouncementBar` would also lift every section on the template out of the page into the header area. Any other string means nothing to anyone, and a misspelt header alias would silently not be a header.

A store may already have a `YandexKit.AnnouncementBar` template made in the cabinet. `section add --template-id` and `section set --template-id` accept it, but report a warning, and the push plan repeats it: the section shows in the header area above the page content, not at the position it takes in the layout. Pass that on before the push.

## Custom header or footer

A store's header or footer can be its own template. The header slot is one composite section, the same id on every page (`parent` on one page — not necessarily the main one — and `child` on the rest; a new page gets the header at 1 and the footer at 9999, older pages often carry the footer right after their last section); a custom header is that same section with widget `YandexKit.Mystique` and a template whose alias is `YandexKit.Header`. The alias is what the server goes by: pages it adds on its own later receive the store header and footer by that alias, and a page that lost its footer gets the main page's custom footer back just as it would a built-in one.

1. Build the markup with `kit-custom-sections` in slot mode (`--slot header|footer --confirm-slot`): its create file then already carries the matching alias. A regular section file without an alias works too — the flag below sets it. The storefront menu is not reachable from here: a header can only point at menus that already exist, by their ids. Take them from `menu_ids` in `section show` of the header slot (`main`, `additional`; the footer reports `legal` and `columns`). When the slot already holds a template, `menu_ids` comes from the template's own menu fields — every field whose schema references `#/definitions/MenuId`, keyed by its setting path (`mainMenu`, `columns[].menu`); `{}` with a `menu_ids_note` when it has none. In the raw settings the main menu id sits at `modules.main.menu.menu`; older settings keep it as a plain string at `modules.main.menu`, and some were wrapped into `menu` objects several times by a former migration, and `menu_ids` is read through every one of those forms. Never put the object at `modules.main.menu` where a template field wants the id.
2. `templates create --file header.json --chrome-slot header --dry-run`. The flag sets `"alias": "YandexKit.Header"` (or `YandexKit.Footer`) in the body; do not edit the file for it. A header or footer alias already in the file must match the flag and is refused without it. The plan carries a `store_wide_warning`: **show it to the user** and get confirmation for exactly that, then rerun with `--confirm`.
3. Put the template into the slot: `section set --work work.json --id SLOT --widget YandexKit.Mystique --template-id T --settings-file s.json` (after `content templates-sync`, or a create with `--work`). It switches every page copy at once, accepts only a template whose alias matches the slot, checks the settings against the template, and reports a `store_wide_warning`, which the push plan repeats. The cabinet's «Заменить» on the header (or footer) does the same. Never report the header as changed after step 2 — only after a push.
4. Once the slot holds a custom template, this skill works with it: `section show` reports `chrome_slot`, `section set --id SLOT --settings-file` edits it on every page, `section set --id SLOT --template-id NEW` moves it to a newer template — which must carry the same alias — and `page add` copies it onto new pages. A header or footer template is refused as a regular section by `section add` and by the push gate.

What to tell the user before they switch: the storefront has behaviour tied to the built-in header that a custom one does not get — the offset of content under a transparent sticky header and the placement of the cookie banner; and applying a store design theme later may replace only built-in headers and footers, leaving a mix. Every correction is one more template, since templates cannot be edited or deleted here. The way back is the same command with the built-in widget: `section set --id SLOT --widget YandexKit.Header --settings-file s.json` (start the settings from `schema widget --name YandexKit.Header --skeleton`; the widget version is stamped from `schema widget-version`). This skill has not tried the switch against a live store: push it with `--no-publish` and have the user look at the proposal on desktop and phone before publishing.

## Media commands

```bash
scripts/kit.py media upload --file ./banner.png --confirm
scripts/kit.py media video-upload --file ./hero.mp4 --confirm
scripts/kit.py media video-status --id vpl...
scripts/kit.py media font-upload --file ./Brand-Bold.woff2 --confirm
```

Image upload returns `url` for use in section `settings`. Video upload returns `video_id`; poll status until `ready`, `error`, or `upload_failed`.

A brand typeface goes into the theme as uploaded files. `media font-upload` takes a local `.woff2`, `.woff`, `.ttf` or `.otf` (up to 100 MB), sends it through the same `POST /files`, requires the server to store it as a generic file (`type: OTHER`), and prints `file_id`, `file_path` and `sources_fragment` — `{"fileId", "filePath"}`, the shape a font source takes. Upload the regular and the bold file at least, then `theme set` with `typography.fontFamily.custom` = `{"isSelected": true, "name": "…", "sources": {"normal": {"regular": <fragment>, "bold": <fragment>}}}` (`medium` and `semibold` optional, `italic` likewise) and `typography.fontFamily.predefined.isSelected` = `false`; check the shape with `schema theme`. Upload only a font the user says they hold a web licence for — a desktop licence usually does not cover a website. The file stays on the storefront host; there is no media deletion here. This skill has not yet rendered an uploaded font on a live storefront: after the theme push, have the user check headings and prices on desktop and phone.

See [`references/workflow.md`](references/workflow.md) for the workflows and [`references/api.md`](references/api.md) for exact routes, projections, schemas, auth, and exit codes.

## Scope boundaries

This skill owns constructor reads, the BFF schema engine, workspace-based storefront editing with confirmed full-replace publication, section-template reads and confirmed creation, local image, video and font upload, and video status. Template update/delete, media deletion, backend catalog lookup, storefront runtime inspection, and source-URL downloads remain unavailable.

**The storefront menu is not reachable from here at all**, neither to read nor to write. It is not part of the constructor content — a header or footer only stores menu ids (`menu_ids` in `section show`) — and this API exposes no menu operations. Two consequences to state rather than work around: a request to change the menu belongs in the cabinet, and a page a menu item links to cannot be removed — the server refuses the whole write with 400 until the item is gone, and this skill cannot tell beforehand which pages the menu links to. Never infer the menu from page titles or aliases and present the guess as fact.

Authoring the html + css + json_schema triple of a custom section is `kit-custom-sections`: it builds and lints the triple offline, then prints the `templates create` and `section` commands of this skill for the upload. This skill uploads and binds what that one produces; it does not author markup itself.
