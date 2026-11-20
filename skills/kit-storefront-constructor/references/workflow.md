# Storefront workflow

Use the smallest fixed-route command that answers the question. Reads, schema lookups, workspace edits, template creation, media uploads, and the confirmed push are separate steps — never mix them into one irreversible action.

## Getting the token in place

`token status` says where the token comes from; `env` adds the contour. When there is none — or a
read answers 401/403 — never put the value on a command line, and where the local page works, use
it instead of asking for the value in the chat.
Run `token web` in the background, give the user the URL it prints, and let them type the token
into the local page; then poll `token status`. In a real terminal `token save` (hidden prompt) does
the same job.

If `token web` exits `2` saying the page is unreachable, this client is not on the user's machine
(container, cloud sandbox, SSH). Do not paper over it with a link they cannot open, and do not leave
them stuck either. Offer the environment or secret settings of that session first (`KIT_TOKEN`), and
if the user cannot reach those settings, accept the token in the conversation and save it with
`token save` from stdin — warning them first that it stays in the history, and telling them to
revoke it in the cabinet afterwards. The command prints that reminder itself after such a save. Either way the file lands at `~/.yandex-kit-skills/kit_api.token` with mode 600, and
`KIT_TOKEN` / `KIT_TOKEN_FILE` in the environment still win over it — the page warns when they are set.

## Compact reads

```bash
scripts/kit.py content summary
scripts/kit.py pages list
scripts/kit.py page show --alias main
scripts/kit.py sections list --page main
scripts/kit.py section show --id UUID
```

Use exact page aliases or UUIDs and fail closed on missing/duplicate matches. `sections list` is the id reference for edits; `section show` also reports every page copy of a composite section. Add `--raw` only when the user explicitly needs a complete payload.

When describing what is on a page, go by each section's `status`: a `hidden` section is stored but not shown to customers. Say which sections are hidden. `section status --id UUID --status published|hidden` changes it in the workspace (with `--page` only on that page); then diff → dry-run → confirm → push as with any edit.

For history: `versions list` (newest ten after local sort), then `versions content --id N` for one snapshot. For custom section schemas: `templates list`, `templates show --id`, `templates versions --id`, and `templates export --id --out DIR` for the html/css/schema sources and its script (`template.js`, empty when the template has none — `script_bytes` in the output tells).

## Editing a section (native or custom — one flow)

1. `sections list --page main` → find the section UUID.
2. `section show --id UUID` → current settings and schema source; for a composite section note that all copies change together.
3. Schema: native → `schema fetch` + `schema widget --name W`; custom → `templates show --id template_id`.
4. `content pull --out work.json`.
5. Write the new settings JSON to a file; `section set --work work.json --id UUID --settings-file s.json`. Validation blocks only errors your edit introduces; existing drift in live data does not block.
6. `content diff --work work.json` → verify the diff is exactly the intended change.
7. `content push --work work.json --commit-message "..." --dry-run` → show the plan to the user.
8. Only after explicit confirmation: `content push ... --confirm`. Report the new version id.

## Changing a published custom section's markup

The markup of a custom section lives in its template, and a live template must never be
edited: the storefront inlines its current text into every content version, so editing it
rewrites the past as well. The section moves to a new template instead.

1. `templates export --id OLD --out DIR` → the current html/css/schema and `template.js` (0 bytes when the template has no script), and `section show --id UUID` → the values the live section actually holds.
2. Build the new template (`kit-custom-sections` does this and lints it offline), carrying the old `script` over into the create file unless the change is meant to drop it, then `templates create --work work.json --confirm` (with a workspace from `content pull`).
3. If the template was created without `--work`, run `content templates-sync --work work.json`: a template created after the pull is not in the snapshot, and the rebind would be refused. Do not pull again for this — it throws away the edits already in the workspace.
4. Write the settings for the new template to a file, mapping the old values onto it by hand. Anything the new schema has no place for is data you are dropping: say so and get a decision, never drop it silently.
5. `section set --work work.json --id UUID --template-id NEW --settings-file s.json` → the section moves on every page it appears on, and the settings are validated against the new template rather than the old one.
6. `content push --work work.json --commit-message "..." --no-publish --confirm` → a stored proposal the storefront does not serve. Have the user look at it in the cabinet's constructor history (you cannot render it; `versions content --id N` is your offline check).
7. Only then publish for real with a plain `content push ... --confirm`.

Step 6 is not optional here. There is no way to load an old version back into a workspace,
so an unwanted rebind cannot be undone by rolling back — the proposal is the only rehearsal.

## Adding a section

Native: `schema widget --name W --skeleton` → fill the skeleton fields → `section add --work work.json --page main --widget W --settings-file s.json [--position N]`. The client stamps `settings.version` from migrate automatically.

Custom: create the template first (below) with `--work work.json` — or run `content templates-sync --work work.json` after it — then `section add --work work.json --page main --widget YandexKit.Mystique --template-id UUID --settings-file s.json`. A header or footer template (alias `YandexKit.Header` / `YandexKit.Footer`) is refused here — it goes into its slot, see below.

Then diff → dry-run → confirm → push, as above.

## Custom header or footer

The header and footer are slots: one composite section per store, copied onto every page. A custom header is a template with alias `YandexKit.Header` sitting in that slot (the footer likewise with `YandexKit.Footer`).

1. `section show --id <header section id>` → the current header settings and `menu_ids` (`main`, `additional`). Those ids are the menus the header points at now; `menu list` shows every menu of the store and `menu show --id` reads one. Take the ids from `menu_ids`, not from the raw settings — the main menu id sits at `modules.main.menu.menu` today, but older settings keep a plain string at `modules.main.menu` and some are wrapped in `menu` several times over; `menu_ids` reads through all of them. A field that wants a menu id gets the string, never the object. When the slot already holds a template, `menu_ids` lists that template's `MenuId` fields by setting path.
2. Build the markup with `kit-custom-sections` in slot mode (`--slot header --confirm-slot`), which writes the matching alias into its create file; a regular section file without an alias also works. Do not add the alias to a create file by hand.
3. `templates create --file header.json --chrome-slot header --dry-run`: the flag sets `"alias": "YandexKit.Header"` in the body. Show the user the plan **including `store_wide_warning`**, in their language. Only after they confirm that store-wide change: the same command with `--confirm`. Record the template id — there is no delete.
4. Switch the slot to the new template in the workspace: `section set --work work.json --id <header section id> --widget YandexKit.Mystique --template-id NEW --settings-file s.json` (the template must already be in the snapshot — `templates create --work`, or `content templates-sync`). Every page copy switches; show the `store_wide_warning`. Then `content push --no-publish --confirm` and let the user look at the proposal before a plain push publishes it. The cabinet's «Заменить» does the same switch by hand.
5. Afterwards the workspace already holds the switched slot, so no new `content pull` is needed (pull again only when the switch was made in the cabinet with «Заменить»): `section show` on the slot reports `chrome_slot: header`; settings edits go through `section set --id SLOT --settings-file`; a newer version of the markup is another `templates create --chrome-slot header` followed by `section set --id SLOT --template-id NEW --settings-file s.json`, then `content push --no-publish` for review, as with any rebind. `page add` copies the custom header onto new pages.

Before step 3, tell the user what a custom header does not inherit from the built-in one (listed in SKILL.md under «Custom header or footer»), and that the way back is `section set --id <slot> --widget YandexKit.Header --settings-file s.json` (settings from `schema widget --name YandexKit.Header --skeleton`).

## Adding a page

1. `pages list` → check the pattern is free and see what already exists.
2. `content pull --out work.json`.
3. `page add --work work.json --title "Акции" --pattern /promo-august` — the client derives `exact` and `variant_type`, generates the id, checks locally every page rule the server refuses a write for, and copies the store header and footer onto the page (`--no-chrome` to opt out). `--status hidden` starts the page unpublished (custom pages support it); `--seo-title/--seo-h1/--seo-description` work on static patterns. An address a system page of this store already answers at — `/account`, `/catalog/sale`, `/products/gift` — is refused: the server would accept it, and the storefront would open either page there. If the store lacks that system page (mostly a store created before that page existed), the page is added with a warning to pass on.
4. Fill the page: `section add --work work.json --page-id <new page UUID> --widget ...` — the new page has no alias, so select it by id from the `page add` report.
5. Diff → dry-run → confirm → push, as with sections. Until the push the page exists only in the workspace.
6. Make the page reachable. Nothing links to a new page: put a link to it into a section of this same edit (a button or a tile), or add a menu item (see «Editing a menu») right after the push — not before, the page does not exist until then and a menu write is live at once.

A system page added with `--alias` arrives with its content widget (the refunds list, the product block, the listing, a titled `RichText` on a text page) — built from the storefront schema, so run `schema fetch` with the store's `--bff-url` first; otherwise the report warns that the page is empty and the widget is added later with `section add`.

A second page for an alias works only for `productCard` and `categoryAndCollection` (`page add --alias productCard --title "Шаблон товара"` → an `alternative` variant on the canonical pattern). That variant is a product/collection page template; assigning it to specific products or collections happens in the B2B cabinet, not here. Without more flags it gets the base widget (`ProductDetails` / `ProductsListing`); `--copy-layout-from default` starts it from the store's current default template instead, sections copied as new ones — the product block or listing included, so the new template's is its own; a block the default template shares with other pages stays shared and is listed in `shared_sections` (with `also_on`): `section set` on it changes every page, and `section remove --page-id <new page>` plus `section add` gives the template its own. Settings drift the default template already has is listed as `inherited_drift` and does not block the push. A template with neither opens the products assigned to it as an empty page — the report says so.

## Renaming, hiding, and SEO of a page

`page set --work work.json --id UUID [--title T] [--status published|hidden] [--seo-title ...] [--clear-meta]`; custom pages may also change `--pattern`. The client refuses what the server would reject — hiding a page whose alias does not support it, SEO meta on a dynamic pattern — and a pattern change on an aliased page, which one backend refuses and another silently overwrites with its own. SEO on the cart, cart preview, search, favourites, unsubscribe and email-confirmation pages and on the account profile, orders, newsletters and refunds pages is stored but never shown — the storefront uses the store-wide SEO template there (the loyalty account page does read its own); the report and the push plan warn, and the user should hear it. On the other static pages `seo_h1` is never printed either. The H1 is the first section title on the page that is not a link (`h1_hint` in `page show`): a banner carousel never carries it, a shelf on a category or collection neither, and a product, listing, blog or cart block takes it first. A loyalty block, or a blog listing without a title, outranks section titles without a heading of its own: the server HTML then has no H1 (`none_in_server_html`). Set it by giving the first titled section the intended title, and keep `also_h1` in `page show` empty — a custom section that writes its own h1 makes a second one. Then the usual diff → dry-run → confirm → push.

## Removing a page

1. `page remove --work work.json --id UUID`. A default system page is refused outright — hide it instead when its alias allows. For everything else the report lists the sections that disappear with the page (last copies vs. composite copies that survive elsewhere) and warns that a page a menu item links to cannot be removed: the server then refuses the whole write with 400 and writes nothing. Check the menus for a link to this page first (`menu list`, `menu show`). For an `alternative` product or collection page template it also carries `assignment_warning`: the products, categories or collections assigned to it fall back to the default template, and no revert or `--no-publish` brings the assignment back.
2. Show that report to the user and get explicit consent for the deletion itself.
3. `content push --work work.json --commit-message "..." --allow-page-removal --dry-run`, then only after confirmation the same with `--confirm`. Without `--allow-page-removal` the push gate refuses — that is the guard against silent page loss, because the server drops unaliased pages without any warning of its own.
4. If the push answers 400 naming the page as linked to a menu item, nothing was written. Remove that item (see «Editing a menu») after telling the user that it vanishes from the live storefront at once and that no content rollback restores it; then repeat the same push. A menu write does not change the content version, so no re-pull is needed unless the push reports `version_conflict`.

## Removing and reordering

`section remove --work work.json --id UUID` removes every copy of the section — for the header or footer slot that is the header or footer of every page, so say that before doing it. `--page ALIAS` (or `--page-id`) takes the section off that one page only and keeps it everywhere else: use it for a shared block that only repeats on a page, such as the main page's FAQ copied onto the catalog. `section move --work work.json --id UUID --position N` reorders within a page (pass `--page` when the section is copied to several pages); the header and footer slots keep their places, and only the content between them is renumbered — except that a footer sitting right after the content moves up to 9999 on that page when the content needs room (`footer_renumbered` in the report; the storefront shows the footer last whatever its number). Then the same diff → dry-run → confirm → push.

## Preview without publishing

When the user wants the edit **saved but not shown** on the storefront:

1. Build the workspace edit exactly as in the flows above (pull → edit commands → diff).
2. `content push --work work.json --commit-message "..." --no-publish --dry-run` → show the plan; it says the write will be stored as an unpublished proposal.
3. After explicit confirmation: `content push ... --no-publish --confirm`.
4. Report **both** numbers from the output: the proposal version (opens in the constructor history by its number) and the active version (a copy of the previous content, live on the storefront). Never phrase it as "applied" or "published".
5. The visual check of the proposal is the user's: you cannot render a stored version. Ask them to open it in the cabinet's constructor history on desktop and phone. Your own check is offline — `versions content --id N` and the design lint on that snapshot. Publishing to look and restoring afterwards is done only with their explicit consent.
6. To publish the proposal later: the working copy still holds it — run a normal `content push --confirm` (the manifest already points at the fresh active version, so there is no 409).

**Exit 4 means the edit went LIVE.** An old server ignores `activate: false` and publishes; the client detects the missing `active_version_id` and says «правка опубликована, режим предпросмотра не поддержан этим стендом». Tell the user immediately that the change is visible on the storefront, and offer to restore the previous content with a new confirmed write; do not retry the preview until the stand is updated.

**Boundary:** the mode rolls back only what lives in the content version (section settings, composition, `template_id`, global settings). Global templates are outside it — previewing a custom section's markup change means creating a **new** template and switching `template_id` in the proposal, never editing the live template. The push gate refuses a workspace with modified `section_templates`.

## Theme and global settings

`schema theme` → prepare the new object → `theme set --work work.json --settings-file gs.json` (validated against the BFF theme schema) → diff → push.

## Template creation

1. Inspect the catalog to avoid duplication, but never treat an alias as unique.
2. Prepare one UTF-8 JSON object with the exact create fields; keep `json_schema` a JSON string decoding to an object. `script` is optional (a copy of an exported template takes it from `template.js`); leave `alias` out unless this is a header or footer.
3. `templates create --file section.json --dry-run` → show the compact plan (sizes, digest, schema fields; `--show-body` only when the source itself has to be read).
4. After explicit confirmation: `templates create --file section.json --confirm` (`--work work.json` adds it to the workspace at once), then verify the UUID via `templates list`.
5. The template is not on the storefront yet: attach it with `section add --widget YandexKit.Mystique --template-id` and push.

Never retry a create automatically after an ambiguous transport outcome — that can duplicate a template. There is no template DELETE; record live-created IDs for manual cleanup through the B2B cabinet.

## Media upload

1. Accept only a local path explicitly supplied by the user; never download a source URL.
2. `media upload --file PATH --confirm` for images, `media video-upload` for supported video.
3. Use the returned `url`/`public_path` in section settings; poll `media video-status --id` until a terminal status.
4. A brand font: confirm the user holds a web licence for it, `media font-upload --file Regular.woff2 --confirm` and the same for the bold file, then `theme set` with `typography.fontFamily.custom` built from the two `sources_fragment`s (`isSelected: true`, `name`, `sources.normal.regular` and `.bold`) and `predefined.isSelected: false` → diff → push. Have the user look at the storefront afterwards.

## Editing a menu

1. `menu list` → the store's menus; `section show --id <header or footer section id>` → `menu_ids`, which of them the storefront shows and where.
2. `menu pull --alias main --work menu.json` (or `--id UUID`), then `menu links` for the ids of pages, categories and collections.
3. Edit `title` and `tree` in `menu.json`. Order in the file is order on the storefront; nesting is `children`.
4. `menu diff --work menu.json` → show the user what is added, removed, relinked and reordered, plus the warnings. A renamed item reads as removed and added, with everything under it.
5. Only after explicit consent: `menu push --work menu.json --confirm`. The result names the snapshot file and the `undo` command — pass both on.
6. On `menu_changed` (exit `1`) nothing was written: pull again, redo the edit, show the new plan.
7. To undo: `menu restore --snapshot FILE --confirm`. When it is refused for a target that no longer exists, `--drop-missing` writes the rest and lists what it left out.

## Changing the logo

1. `logo show` → what the store has now.
2. `logo set --file ./logo.png --dry-run`, then after explicit consent the same with `--confirm`. It saves the previous logo to a snapshot file, uploads the new one and writes it.
3. Tell the user the change is live at once and give the `undo` command from the result (`logo restore --snapshot FILE --confirm`).
4. A header on a dark background shows a separate inverted logo when the store has one; that one is set in the cabinet and does not follow this change.

## Push failure handling

- Failed gates exit `2` with the list of violated gates and no request. The workspace is wrong — fix it through commands or re-pull; do not weaken a gate.
- Unconfirmed push exits `3` offline; ask the user for confirmation, never assume it.
- `warnings` in the plan never block the push; relay every one before asking for confirmation. They name: stored custom pages whose address a newer server rule refuses (the push may still go through; if it then fails with 400 for that address, nothing was written — offer `page set --pattern` for that page, saying that links to the old address stop working); SEO set on a page whose own SEO the storefront never shows; a custom address a system page the store does not have yet would also serve, or a new system page over a stored custom address; a section added or rebound onto a `YandexKit.AnnouncementBar` template, which shows in the header area instead of its place on the page.
- HTTP 400 «Превышено максимальное количество …» means the edit crossed one of the store's caps — pages, sections on one page, or modules in one section; the message names which and the maximum. Nothing was written. Say so, shrink the edit, and ask for fresh confirmation.
- HTTP 409 means the storefront moved past the pulled base: the client rereads once and stops. Re-pull, reapply the edit, request fresh confirmation. No automatic retry, ever.
- Exit 4 after `--no-publish` means the stand runs an old server and the edit **was published**. Say so in plain words, name the published version, and never call the result a preview.
- A written-counts warning after success means the server wrote different totals than expected locally: inspect the storefront immediately and tell the user exactly what diverged.
- Malformed responses fail closed rather than being guessed into shape. HTTP 401/403 means token or store-scope failure; never print the token. Offer a replacement through `token web`, not by asking for the value.
