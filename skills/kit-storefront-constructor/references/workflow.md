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

For history: `versions list` (newest ten after local sort), then `versions content --id N` for one snapshot. For custom section schemas: `templates list`, `templates show --id`, `templates versions --id`, and `templates export --id --out DIR` for the html/css/schema sources.

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

1. `templates export --id OLD --out DIR` → the current html/css/schema, and `section show --id UUID` → the values the live section actually holds.
2. Build the new template (`kit-custom-sections` does this and lints it offline), then `templates create --confirm`.
3. `content pull --out work.json` **again** — a template created after the previous pull is not in that snapshot, and the rebind would be refused.
4. Write the settings for the new template to a file, mapping the old values onto it by hand. Anything the new schema has no place for is data you are dropping: say so and get a decision, never drop it silently.
5. `section set --work work.json --id UUID --template-id NEW --settings-file s.json` → the section moves on every page it appears on, and the settings are validated against the new template rather than the old one.
6. `content push --work work.json --commit-message "..." --no-publish --confirm` → a stored proposal the storefront does not serve. Have a person look at it.
7. Only then publish for real with a plain `content push ... --confirm`.

Step 6 is not optional here. There is no way to load an old version back into a workspace,
so an unwanted rebind cannot be undone by rolling back — the proposal is the only rehearsal.

## Adding a section

Native: `schema widget --name W --skeleton` → fill the skeleton fields → `section add --work work.json --page main --widget W --settings-file s.json [--position N]`. The client stamps `settings.version` from migrate automatically.

Custom: create the template first (below), verify its UUID in `templates list`, then `section add --work work.json --page main --widget YandexKit.Mystique --template-id UUID --settings-file s.json`.

Then diff → dry-run → confirm → push, as above.

## Adding a page

1. `pages list` → check the pattern is free and see what already exists.
2. `content pull --out work.json`.
3. `page add --work work.json --title "Акции" --pattern /promo-august` — the client derives `exact` and `variant_type`, generates the id, validates every server rule locally, and copies the store header and footer onto the page (`--no-chrome` to opt out). `--status hidden` starts the page unpublished (custom pages support it); `--seo-title/--seo-h1/--seo-description` work on static patterns.
4. Fill the page: `section add --work work.json --page-id <new page UUID> --widget ...` — the new page has no alias, so select it by id from the `page add` report.
5. Diff → dry-run → confirm → push, as with sections. Until the push the page exists only in the workspace.

A second page for an alias works only for `productCard` and `categoryAndCollection` (`page add --alias productCard --title "Шаблон товара"` → an `alternative` variant on the canonical pattern). That variant is a product/collection page template; assigning it to specific products or collections happens in the B2B cabinet, not here.

## Renaming, hiding, and SEO of a page

`page set --work work.json --id UUID [--title T] [--status published|hidden] [--seo-title ...] [--clear-meta]`; custom pages may also change `--pattern`. The client refuses what the server would reject: hiding a page whose alias does not support it, SEO meta on a dynamic pattern, pattern changes on aliased pages. Then the usual diff → dry-run → confirm → push.

## Removing a page

1. `page remove --work work.json --id UUID`. A default system page is refused outright — hide it instead when its alias allows. For everything else the report lists the sections that disappear with the page (last copies vs. composite copies that survive elsewhere) and warns that the server also deletes menu items pointing at the page.
2. Show that report to the user and get explicit consent for the deletion itself.
3. `content push --work work.json --commit-message "..." --allow-page-removal --dry-run`, then only after confirmation the same with `--confirm`. Without `--allow-page-removal` the push gate refuses — that is the guard against silent page loss, because the server drops unaliased pages without any warning of its own.

## Removing and reordering

`section remove --work work.json --id UUID` removes every copy of the section. `section move --work work.json --id UUID --position N` reorders within a page (pass `--page` when the section is copied to several pages). Then the same diff → dry-run → confirm → push.

## Preview without publishing

When the user wants the edit **saved but not shown** on the storefront:

1. Build the workspace edit exactly as in the flows above (pull → edit commands → diff).
2. `content push --work work.json --commit-message "..." --no-publish --dry-run` → show the plan; it says the write will be stored as an unpublished proposal.
3. After explicit confirmation: `content push ... --no-publish --confirm`.
4. Report **both** numbers from the output: the proposal version (opens in the constructor history by its number) and the active version (a copy of the previous content, live on the storefront). Never phrase it as "applied" or "published".
5. To publish the proposal later: the working copy still holds it — run a normal `content push --confirm` (the manifest already points at the fresh active version, so there is no 409).

**Exit 4 means the edit went LIVE.** An old server ignores `activate: false` and publishes; the client detects the missing `active_version_id` and says «правка опубликована, режим предпросмотра не поддержан этим стендом». Tell the user immediately that the change is visible on the storefront, and offer to restore the previous content with a new confirmed write; do not retry the preview until the stand is updated.

**Boundary:** the mode rolls back only what lives in the content version (section settings, composition, `template_id`, global settings). Global templates are outside it — previewing a custom section's markup change means creating a **new** template and switching `template_id` in the proposal, never editing the live template. The push gate refuses a workspace with modified `section_templates`.

## Theme and global settings

`schema theme` → prepare the new object → `theme set --work work.json --settings-file gs.json` (validated against the BFF theme schema) → diff → push.

## Template creation

1. Inspect the catalog to avoid duplication, but never treat an alias as unique.
2. Prepare one UTF-8 JSON object with the exact create fields; keep `json_schema` a JSON string decoding to an object.
3. `templates create --file section.json --dry-run` → show the exact body.
4. After explicit confirmation: `templates create --file section.json --confirm`, then verify the UUID via `templates list`.
5. The template is not on the storefront yet: attach it with `section add --widget YandexKit.Mystique --template-id` and push.

Never retry a create automatically after an ambiguous transport outcome — that can duplicate a template. There is no template DELETE; record live-created IDs for manual cleanup through the B2B cabinet.

## Media upload

1. Accept only a local path explicitly supplied by the user; never download a source URL.
2. `media upload --file PATH --confirm` for images, `media video-upload` for supported video.
3. Use the returned `url`/`public_path` in section settings; poll `media video-status --id` until a terminal status.

## Push failure handling

- Failed gates exit `2` with the list of violated gates and no request. The workspace is wrong — fix it through commands or re-pull; do not weaken a gate.
- Unconfirmed push exits `3` offline; ask the user for confirmation, never assume it.
- HTTP 409 means the storefront moved past the pulled base: the client rereads once and stops. Re-pull, reapply the edit, request fresh confirmation. No automatic retry, ever.
- Exit 4 after `--no-publish` means the stand runs an old server and the edit **was published**. Say so in plain words, name the published version, and never call the result a preview.
- A written-counts warning after success means the server wrote different totals than expected locally: inspect the storefront immediately and tell the user exactly what diverged.
- Malformed responses fail closed rather than being guessed into shape. HTTP 401/403 means token or store-scope failure; never print the token. Offer a replacement through `token web`, not by asking for the value.
