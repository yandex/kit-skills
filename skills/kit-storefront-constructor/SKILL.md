---
name: kit-storefront-constructor
description: |
  Manage a Яндекс.Кит storefront through compact Experimental API projections: read pages, sections, versions, and section-template schemas; inspect native-widget schemas from the storefront BFF and build valid settings skeletons; edit sections, pages layout, and global settings in a local write workspace; create, retitle, hide, or remove storefront pages with server-mirroring local validation; publish through a confirmed full-replace write with local integrity gates, or keep the result as an unpublished proposal version that leaves the storefront untouched; create validated custom section templates; upload local storefront images and videos. Also sets up the store API token: locally through a one-shot page on 127.0.0.1, so it is never pasted into the chat, and in a hosted session through that session's environment settings or a token sent in the conversation.
user-invocable: true
allowed-tools: Bash(scripts/kit.py:*)
---

When editing this skill or its scripts, first read [`AGENTS.md`](AGENTS.md), which in turn points at the repository contract.

# Яндекс.Кит storefront constructor

Use the fixed-route, Python stdlib client in `scripts/kit.py`. It downloads endpoint responses internally but returns compact local projections by default. Full storefront payloads (~660 KB live) live in a local workspace on disk — never in your context.

## Hard rules

1. Use only routes exposed by `scripts/kit.py`; do not use generic HTTP clients, other routes or write methods, UI scraping, or browser automation.
2. Two backends, two URLs. Core experimental API: `KIT_API_BASE_URL` / `--base-url`, store-scoped Bearer token. Storefront BFF (schema + migrate, no auth): `KIT_BFF_BASE_URL` / `--bff-url`, set to the store's own storefront origin, which serves `/constructor-api`. The client derives it only when the Core API shares that host; otherwise it refuses. **Get the origin from the store itself — `kit.py store` returns it as `b2c_url`** — never guess it and never assemble it from the store slug, because a store on its own domain has a different `b2c_url`.
3. Resolve the token through `KIT_TOKEN`, then `KIT_TOKEN_FILE`, then `~/.yandex-kit-skills/kit_api.token`. Never print, log, commit, prompt with, or pass it as an argument.
4. Prefer compact commands. Use `--raw` and `versions list --all` only for a deliberate high-volume diagnostic.
5. Select pages by exact `alias` or UUID `id`; sections and templates by UUID only. Never infer identity.
6. Every mutation — template create, media upload, and `content push` — requires explicit per-operation `--confirm`. Dry runs and unconfirmed mutations perform no token loading and no network request.
7. The only write transport is the full-replace `PUT /constructor/content` behind `content push`. There is no partial write; the push gates run locally before any confirmation, and a failed gate means the workspace is wrong — never bypass a gate by hand-editing.
8. Never edit `work.json` by hand. All edits go through `section set/add/remove/move` and `theme set`, which validate settings against the authoritative schema at edit time and keep composite copies in sync.
9. `migrate` is called by the client with strictly empty settings, only to learn a widget's actual version. Never send real settings to migrate: on live data it overwrites content with placeholders.
10. Preserve the write safety invariants: read-before-write (`content pull`), CAS through `base_version_id`, one reread on 409, and no automatic retry of any mutation.
11. Template update/delete, media deletion, and mass-destructive operations are unavailable. Refuse them without an API call and explain that the operation is «не доступна».
12. Upload only a local file path supplied by the user. Never fetch source media from a URL. Images support non-SVG/non-ICO `image/*`; videos support mp4, mov, webm, avi, and flv; the maximum is 104857600 bytes.
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
scripts/kit.py section show --id 39f00b57-4683-4da7-8879-26639f5c83b2
scripts/kit.py versions list [--limit 5] [--since 2026-08-01T00:00:00Z]
scripts/kit.py versions content --id 42
scripts/kit.py templates list
scripts/kit.py templates show --id UUID
scripts/kit.py templates versions --id UUID [--version-id UUID]
scripts/kit.py templates export --id UUID --out DIR
scripts/kit.py content preview --template-id UUID
```

`sections list` returns section UUIDs — the reference for any edit. `section show` returns one section's settings, its schema source, and **every page copy**: a composite section (header/footer) is stored once and copied to all pages; the backend keeps the parent copy's settings.

`section set --template-id NEW` moves a published custom section onto another template, on every page it appears on, and checks the settings against the template it moves to. This is how a custom section's markup is changed, because the live template cannot be edited in place. Settings stay mandatory: state what the fields hold under the new template instead of letting the old values land in a shape that may have no room for them. Pull again after `templates create` — a template minted after the last pull is not in the snapshot yet.

## Native widget schemas (BFF)

```bash
scripts/kit.py schema fetch                          # cache by ETag; other schema commands work offline
scripts/kit.py schema widgets
scripts/kit.py schema widget --name YandexKit.Faq
scripts/kit.py schema widget --name YandexKit.Faq --skeleton
scripts/kit.py schema theme
scripts/kit.py schema widget-version --name YandexKit.Faq
```

Run `schema fetch` once per session before editing native sections. `--skeleton` builds a locally validated settings object from schema defaults — the correct starting point for a new native section. `widget-version` reads the widget's actual `settings.version` through migrate with strictly empty settings; widgets without migrations have none.

## Editing the storefront (write workspace)

```bash
scripts/kit.py content pull --out work.json
scripts/kit.py page add       --work work.json --title "Акции" --pattern /promo [--alias A] [--status hidden] [--seo-title ...] [--no-chrome]
scripts/kit.py page set       --work work.json --id UUID [--title T] [--pattern P] [--status S] [--seo-title ...] [--clear-meta]
scripts/kit.py page remove    --work work.json --id UUID
scripts/kit.py section set    --work work.json --id UUID --settings-file s.json
scripts/kit.py section add    --work work.json --page main --widget YandexKit.Faq --settings-file s.json [--position N]
scripts/kit.py section add    --work work.json --page main --widget YandexKit.Mystique --template-id UUID --settings-file s.json
scripts/kit.py section remove --work work.json --id UUID
scripts/kit.py section move   --work work.json --id UUID --position N [--page alias]
scripts/kit.py theme set      --work work.json --settings-file gs.json
scripts/kit.py content diff   --work work.json
scripts/kit.py content push   --work work.json --commit-message "..." --dry-run
scripts/kit.py content push   --work work.json --commit-message "..." --confirm
scripts/kit.py content push   --work work.json --commit-message "..." --no-publish --confirm
```

The flow is always: **pull → edit through commands → diff → dry-run → human confirmation → push --confirm**. Without `--out`, `pull` writes to the per-store workspace under `~/.yandex-kit-skills/workspace/` (override: `KIT_WORKSPACE_DIR`).

- `section set` rewrites **all copies** of a composite section at once and preserves `settings.version` of an existing section — changing a version is a migration and is not supported.
- `section add` for a native widget stamps `settings.version` from `schema widget-version` automatically; widgets without migrations get no version field.
- Edit-time validation ignores schema drift that already exists in the pulled data; only errors your edit introduces are blocked.
- `content push` runs local gates before anything else: the base manifest exists, no page is lost (a default page with an alias can never be dropped; other removals need `--allow-page-removal`), no alias changed, added/changed widgets exist, changed settings validate, composite copies are in sync. `--dry-run` and refusal happen completely offline.
- The push plan shows the diff summary, never the full payload. Show it to the user and get explicit confirmation before `--confirm`.
- On 409 the client rereads once, reports `version_conflict`, and stops. Re-pull, reapply, and ask for fresh confirmation — never retry automatically.
- The success response is compact: new version, `pages_count`, `sections_count`. The client compares them with local expectations and warns loudly on divergence.
- After every successful push the client rebases the workspace onto the version the server reports as active (`active_version_id`), so the next push carries a fresh CAS base instead of hitting 409.

### Pages: what a human chooses and what is derived

A page has three human-owned dimensions — **title**, **pattern** (URL route; free for custom pages, fixed for aliased system pages), and **status** (`published`/`hidden`; `hidden` is honored only for custom pages and a fixed subset of aliases). Static pages (pattern without `:param`) may also carry SEO meta. Everything else is derived and never set by hand: `exact` follows the pattern, `variant_type` is recomputed by the server, and the page `id` is generated. Several pages per alias exist only for `productCard` and `categoryAndCollection` — those alternatives are the «page templates» merchants assign to individual products and collections in the B2B cabinet; the assignment itself is a separate entity outside this skill.

- `page add` derives everything derivable, validates against the same rules the server enforces (canonical patterns, reserved patterns, uniqueness, one default per alias, hidden subset, meta-on-static), and by default copies the store header and footer onto the new page (`--no-chrome` to skip). New pages exist only in the workspace until a confirmed push; select them in section commands by `--page-id`.
- `page set` changes title, status, SEO meta, and — for custom pages only — the pattern. Aliases and variant types are immutable.
- `page remove` refuses default system pages outright (the server 400s the whole write) and for everything else spells out exactly what disappears: the page, its last-copy sections, and — server-side — menu items pointing at it. The push still demands `--allow-page-removal` plus explicit confirmation; remember that a page without an alias would otherwise be dropped **silently** by any full replace.

### Write without publishing (`--no-publish`)

`content push --no-publish --confirm` sends the same full-replace body with `activate: false`. In **one server transaction** the edit is stored as a proposal version and a copy of the previously active content is re-activated — the storefront never shows the proposal, not even for a moment. The response carries both numbers: `version` (the proposal) and `active_version_id` (what is live now).

- Report **both** numbers to the user and **never** say the change is applied or visible. The correct wording names the proposal version and the version currently on the storefront; the proposal opens in the constructor history by its number.
- The workspace manifest moves to `active_version_id`; the working copy keeps the proposal, so a follow-up confirmed `content push` (without `--no-publish`) publishes exactly that proposal.
- **Old-server guard.** A server without this rollout silently ignores `activate` and **publishes the edit**. The only reliable signal is the response: if it carries no `active_version_id`, the client reports «правка опубликована, режим предпросмотра не поддержан этим стендом» and exits `4`. Exit 4 means the change is LIVE on the storefront right now — tell the user immediately and never present it as a preview or retry silently.

### What the no-publish mode can and cannot roll back

Everything that lives in the content version rolls back honestly: section settings, section composition, `template_id` values, global settings. **Global section templates do not**: the write transport does not carry them, and reading any version returns the *current* templates. Editing a live template changes the storefront immediately and in every version — reverting a version does not undo it. Therefore:

- To preview a markup (html/css/schema) change of a custom section: create a **new** template with `templates create`, then switch the section's `template_id` to it in the proposal. Never modify a live template for a preview. An unreferenced template renders nowhere, so creating one is harmless.
- The push gate enforces the boundary: a workspace whose `section_templates` differ from the base snapshot is refused locally — such a change could neither be written nor previewed through push.

## Create a custom template

Prepare one UTF-8 JSON object with non-empty string fields `title`, `html`, `css`, and `commit_message`; `json_schema` must be a string containing valid JSON; `alias` is optional.

```bash
scripts/kit.py templates create --file section.json --dry-run
scripts/kit.py templates create --file section.json --confirm
scripts/kit.py templates list
```

Always show the dry-run preview before asking for confirmation. Creating a template does **not** attach it to any page: attach it afterwards with `section add --widget YandexKit.Mystique --template-id` plus `content push`. There is no DELETE route; record every live-created ID for manual cleanup through the B2B cabinet.

## Media commands

```bash
scripts/kit.py media upload --file ./banner.png --confirm
scripts/kit.py media video-upload --file ./hero.mp4 --confirm
scripts/kit.py media video-status --id vpl...
```

Image upload returns `url` for use in section `settings`. Video upload returns `video_id`; poll status until `ready`, `error`, or `upload_failed`.

See [`references/workflow.md`](references/workflow.md) for the workflows and [`references/api.md`](references/api.md) for exact routes, projections, schemas, auth, and exit codes.

## Scope boundaries

This skill owns constructor reads, the BFF schema engine, workspace-based storefront editing with confirmed full-replace publication, section-template reads and confirmed creation, local image/video upload, and video status. Template update/delete, media deletion, backend catalog lookup, storefront runtime inspection, and source-URL downloads remain unavailable.

**The storefront menu is not reachable from here at all**, neither to read nor to write. It is not part of the constructor content — a header only stores the menu's id in `modules.main.menu` — and this API exposes no menu operations. Two consequences to state rather than work around: a request to change the menu belongs in the cabinet, and when a page is removed the server silently deletes the menu items pointing at it, which this skill cannot list beforehand. Never infer the menu from page titles or aliases and present the guess as fact.

Authoring the html + css + json_schema triple of a custom section is `kit-custom-sections`: it builds and lints the triple offline, then prints the `templates create` and `section` commands of this skill for the upload. This skill uploads and binds what that one produces; it does not author markup itself.
