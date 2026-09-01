# API contract

The client is fixed to the routes below. Two backends:

* **Core experimental API** under `/api/experimental/v1` — store-scoped Bearer token (`KIT_API_BASE_URL` / `--base-url`).
* **Storefront BFF** under `/constructor-api` — no authentication (`KIT_BFF_BASE_URL` / `--bff-url`, set to the store's storefront origin). Derivation from the Core API URL works only when both share a host; on production the Core API is a separate gateway, so read the origin from `kit.py store` → `b2c_url`. Take that value as-is: a store on its own domain has a `b2c_url` that cannot be assembled from its slug. When neither is set and derivation fails the client refuses with instructions; it never guesses silently.

## Core read operations

| Client command | Method and path | Operation ID | Default output |
|---|---|---|---|
| `content summary` / `content latest` | `GET /constructor/content` | `GetConstructorContent` | page/status/template counts |
| `pages list` | `GET /constructor/content` | `GetConstructorContent` | compact page records |
| `page show` | `GET /constructor/content` | `GetConstructorContent` | one page projection with section `id`s |
| `sections list --page` | `GET /constructor/content` | `GetConstructorContent` | flat section inventory: id, widget, sequence, type, page copies |
| `section show --id` | `GET /constructor/content` | `GetConstructorContent` | one section's settings, schema source, all page copies |
| `versions list` | `GET /constructor/versions` | `GetConstructorVersions` | newest ten versions |
| `versions content --id N` | `GET /constructor/versions/{id}/content` | `GetConstructorVersionContent` | unvalidated complete snapshot (printed as returned) |
| `content preview --template-id UUID` | `GET /constructor/content/template?template_id=UUID` | `PreviewConstructorWithTemplate` | compact preview |
| `templates list` | `GET /section-templates` | `GetSectionTemplates` | compact template catalog |
| `templates show --id UUID` | `GET /section-templates` | `GetSectionTemplates` | one exact full schema record |
| `templates versions --id UUID` | `GET /section-templates/{id}/versions` | `GetSectionTemplateVersions` | version history records |
| `templates versions --id --version-id` | `GET /section-templates/{id}/versions/{vid}` | `GetSectionTemplateVersion` | one historical template record |
| `templates export --id --out DIR` | `GET /section-templates` | `GetSectionTemplates` | writes `template.html/.css/.schema.json/.meta.json` |
| `media video-status --id ID` | `GET /videos/{id}` | `GetVideo` | asynchronous status |

Compact template-list records include exactly `id`, `title`, `json_schema_bytes`, and `alias` (null when absent). `templates show` adds the exact `json_schema` string. Aliases are optional and non-unique; UUID is the only identity key.

Timestamps are parsed as RFC-3339 with 1-9 fractional digits and either `Z` or a numeric offset; the fraction is truncated to microseconds and every instant is normalized to UTC before sorting or `--since` comparison.

## BFF schema engine

| Client command | Method and path | Notes |
|---|---|---|
| `schema fetch [--force]` | `GET /constructor-api/schema` | ETag-conditional; cached under `~/.yandex-kit-skills/cache` (override `KIT_SCHEMA_CACHE_DIR`) |
| `schema widgets` | — (cache) | widget names, ref, field count, `has_version_field` |
| `schema widget --name W [--skeleton]` | — (cache) | compact resolved schema; `--skeleton` builds a locally validated settings object from defaults |
| `schema theme` | — (cache) | compact `globalSettings` schema |
| `schema widget-version --name W` | `POST /constructor-api/migrate` | body is strictly `{"sections":[{"key":"version-probe","widget":W,"settings":{}}]}`; only `settings.version` is read from the response |

The local validator implements exactly the draft-07 subset present in the live document: `$ref` (`#/definitions/...`), `type`, `properties`, `required`, `items`, `enum`, `const`, `minimum`, `maximum`, `anyOf`, `not`, `additionalProperties`. A document using any other constraint keyword (`oneOf`, `allOf`, `if/then/else`, `patternProperties`) is rejected outright rather than checked partially.

Custom-template `json_schema` documents resolve `#/definitions/...` refs against their own definitions first, then against the BFF definitions (editor models such as `ImageModel`, `LinkSource`, `TextArea`). Refs under `#/definitions/partials/...` point at server-side template fragments and are treated as unconstrained.

**Never send real settings to migrate.** On live data it replaces content with demo placeholders; its only safe role is reporting a widget's actual version on empty settings.

## Write workspace and full-replace push

`content pull --out WORK` snapshots the full content into `WORK` plus a pristine `WORK.base.json` and `WORK.manifest.json` (`base_version_id`, sha256 of the base, source URL, counts). Editing commands (`page add/set/remove`, `section set/add/remove/move`, `theme set`) mutate `WORK` only, validate at edit time, and print a compact report. `content diff --work WORK` is a fully offline diff against the base; sections of an added page are reported as additions.

### Page model

There is **no page endpoint**: pages travel only inside the full-replace body, exactly as the B2B cabinet writes them. A page carries `id` (UUID), `title`, `pattern`, `exact`, optional `alias`, `variant_type`, `status`, optional `meta` (`seo_title`/`seo_h1`/`seo_description`), optional `static_context`, and `layout`. Server rules mirrored locally by `kitlib/pages.py`:

* **Pattern** is normalized like the server does (leading `/` added, one trailing `/` stripped). An aliased page must use its alias's canonical pattern; a custom page must not use a canonical pattern (the server would silently assign the alias) and gets exactly one pattern (`~`-joined multi-patterns exist only on `categoryAndCollection`). Patterns are unique across pages, except several `productCard` / `categoryAndCollection` variants sharing their canonical pattern.
* **Alias** comes from a fixed catalog of 28 system aliases and is immutable on an existing page. At most one `default` variant per alias; extra pages per alias exist only for `productCard` and `categoryAndCollection` (product/collection page templates, assigned in the B2B cabinet — the assignment is not part of constructor content).
* **`variant_type`** and **`exact`** are derived — the server recomputes the variant on write and derives `exact` from the pattern on read; the client refuses hand-set values that contradict the derivation.
* **`status`** is `published`/`hidden`; `hidden` is accepted only for custom pages (no alias) and the aliases `accountLoyalty`, `blog`, `blogEntry`, `catalog`, `favouriteProducts`, `giftCard`, `giftCardQuestions`, `loyaltyLanding`, `loyaltyOffer`, `subscription`. Every other page is always effectively published.
* **`meta`** (SEO) is allowed only on static pages (`exact: true`); the server keeps robots/social/twitter meta of an existing page and takes only the three SEO fields from the write.
* A **default page with an alias can never be dropped** (400 for the whole transaction); a page missing from the body is deleted, and a page without an alias is deleted **silently** together with menu items pointing at it.
* Not checkable locally: a pattern colliding with an SEO **redirect** page — the server rejects that write with 400 and names the pattern; resolve it in the cabinet under Настройки → SEO → Редиректы.

Page-level drift already present in the pulled data never blocks a push (same baseline principle as section settings); only pages the edit added or changed are validated.

`content push --work WORK --commit-message M [--dry-run | --confirm] [--allow-page-removal] [--no-publish]` sends `PUT /constructor/content` (`UpdateConstructorContent`) with the body:

```json
{"pages": [...], "global_settings": {...}, "base_version_id": N, "commit_message": "M", "activate": false}
```

`activate` (optional, default `true`) is included only when `--no-publish` is passed. With `activate: false` the server, in one transaction, stores the body as a proposal version and re-activates a copy of the previously active content — the storefront never changes.

Pages are converted from the read shape by dropping `categories_count`, `collections_count`, and `product_cards_count`; everything else, including `instance_id`, is carried through. The success response is compact: `{"version": {...}, "active_version_id": K, "pages_count": N, "sections_count": M}`; the client compares the counts against local expectations and warns loudly on divergence. Extra unknown response fields are tolerated — validators must never demand "no extra fields", because the contract grows (that is exactly how `active_version_id` arrived).

`active_version_id` is the version active after the write. With `activate: true` it equals `version.id`; with `activate: false` it is the server-made copy of the previous content. **An old server does not send it and silently publishes despite `activate: false`** — when `--no-publish` was requested and the response has no `active_version_id`, the client reports that the edit was published and the preview mode is unsupported, and exits `4`.

After every successful push the client rebases the workspace: the manifest's `base_version_id` moves to `active_version_id`. On a published write the base snapshot becomes the written working copy; on a no-publish write the base keeps its content (that is what stayed active) and only its version id moves, while the working copy keeps the proposal — so `content diff` still shows it and a follow-up publish push carries a fresh CAS base instead of hitting 409.

Local gates, all before any confirmation prompt, token load, or network access:

1. the workspace manifest exists and matches its base snapshot digest;
2. no base page is missing from the body — a page with an alias and `variant_type: default` can never be dropped (the server rejects the whole transaction with 400); other removals require `--allow-page-removal`;
3. no page alias changed;
4. every added or changed page passes the page model rules above (unique ids and patterns, canonical/reserved patterns, variant derivation, status subset, meta on static pages only);
5. every added or changed section's widget exists in the cached BFF schema, or is `YandexKit.Mystique` with a `template_id` found in the pulled catalog;
6. changed settings validate against their schema — but only errors introduced by the edit block; drift already present in the pulled data does not;
7. composite copies of one section id carry identical settings on every page;
8. `section_templates` in the workspace are byte-identical to the base snapshot — the write transport does not carry templates, so a template edit could neither be written nor previewed; markup changes go through a new template plus a `template_id` rebind.

Full-replace semantics (server side): a page missing from the body is not carried into the new version; custom pages without an alias are dropped silently together with menu items pointing at them; section templates are not transmitted — the server keeps the existing ones, and reading any version returns the current templates (a template edit affects every version and is not undone by reverting); a composite section is stored once per storefront and the parent copy's settings win.

`--dry-run` prints the gate-checked plan (diff summary, never the payload) and exits 0 offline. Unconfirmed push exits `3` offline. On 409 the client rereads content once, reports `version_conflict` with both versions, and exits `1` without retrying.

## Create a section template

`templates create --file PATH --confirm` sends one `POST /section-templates` (`CreateSectionTemplate`). Required non-empty strings: `title`, `html`, `css`, `json_schema` (a JSON string decoding to an object), `commit_message`; `alias` optional. Unknown fields are rejected. `--dry-run` validates and prints the exact body without token or network access. Creation does not attach the template to a page; attachment is `section add --widget YandexKit.Mystique --template-id` plus `content push`. Update and delete are unavailable.

## Media uploads

| Client command | Method and path | Operation ID | Contract |
|---|---|---|---|
| `media upload --file PATH --confirm` | `POST /files` | `UploadFile` | multipart `file`; returns `public_path` and normalized `url` |
| `media video-upload --file PATH --confirm` | `POST /videos` | `UploadVideo` | multipart `file`; returns opaque `video_id` |

Uploads read only a user-provided local file. Source URLs are rejected. The maximum size is 104857600 bytes. Images must be supported non-SVG/non-ICO `image/*`; videos support mp4, mov, webm, avi, and flv.

## Authentication and contour selection

Token resolution order: `KIT_TOKEN`, `KIT_TOKEN_FILE`, `~/.yandex-kit-skills/kit_api.token`. Tokens are trimmed, must contain no whitespace, and are never printed. The BFF is unauthenticated; no token is ever sent to it.

Three ways to fill the token file, one validation rule (`yakit_` prefix, no whitespace) shared by all of them:

| Command | Input | Notes |
|---|---|---|
| `token save` | hidden terminal prompt, or stdin when piped | needs a TTY or a pipe; a pipe exposes the value to whatever produced it |
| `token web` | a form on a one-shot local page | the value goes browser → token file, never through the agent; needs the client and the browser on one machine |
| `token save` from stdin | the user sends the value in the conversation, as text or as a file | the hosted-session route when the environment settings are out of reach: it works everywhere, and the value stays in the conversation history — say so, then tell the user they can revoke the token afterwards |
| — | `KIT_TOKEN` / `KIT_TOKEN_FILE` set by the user | takes precedence over the file; `token web` says so on the page |

`token web` opens `http://127.0.0.1:<port>/?secret=<one-time>` (`--port 0` by default picks a free port) and makes no network call of any kind. Before binding anything it checks that the page could reach a browser on this host — SSH environment variables, container markers (`/.dockerenv`, `/run/.containerenv`, PID 1 cgroups), a Linux session without a display, or no registered browser — and refuses with exit `2` and instructions when the answer is no, because in a hosted session the printed link resolves to the *user's* machine, where nothing is listening. `--force` skips that check for a user who forwards the port themselves. Its guards: loopback bind, loopback peer **and** `Host` header (which is what refuses a DNS-rebinding page), a 256-bit URL secret compared in constant time and capped at 20 wrong attempts, request logging off, the token accepted only in a POST body, a page with `default-src 'none'` and no external resource, a body-size cap, one successful save then exit, and a `--timeout` deadline (default 300 s, max 3600). Exit `0` when a token was saved, `2` on timeout, refusal, or a port that cannot be bound. `--no-open` prints the URL without launching a browser.

## Failure behavior

| Exit | Meaning |
|---|---|
| `0` | success |
| `1` | HTTP/API or network failure (including 409 after the single reread); JSON error envelope on stderr |
| `2` | local validation/configuration failure, including failed push gates |
| `3` | mutation refused because confirmation is absent or the operation is unavailable |
| `4` | the write succeeded but the requested no-publish mode was not honored: the server ignored `activate: false` and **published the edit** (no `active_version_id` in the response). The change is live on the storefront |

HTTP 401/403 is reported without exposing the token. Responses are JSON-validated and fail closed on malformed required fields.
