# kit-storefront-constructor

**First read** parent [../../AGENTS.md](../../AGENTS.md).

**Contract:** [kit-storefront-constructor SKILL](./SKILL.md) — full rules.
Routes, projections, and exit codes: [./references/api.md](./references/api.md).

## Safety

- Never add a route that is not already exposed by `./scripts/kit.py`. No generic HTTP
  client, no UI scraping, no browser automation, no alternate transport.
- Never add a mutating code path without an explicit, per-operation `--confirm` gate.
  Dry runs and unconfirmed mutations must perform no token load and no network call.
- The only write transport is the full-replace `PUT /constructor/content` behind
  `content push`. No partial section write, no alternate write route.
  The push gates in `scripts/kitlib/write.py` run locally before confirmation, token,
  and network; never weaken or bypass a gate, and never write a body assembled outside
  the workspace commands.
- Write invariants: read-before-write (`content pull`), CAS through `base_version_id`,
  one reread on 409, no automatic retry of any mutation, and a workspace rebase onto the
  reported `active_version_id` after every successful push.
- The no-publish mode (`content push --no-publish`, body field `activate: false`) is
  server-side and atomic. Its safety rests on **verifying the response**, not the request:
  an old server silently ignores `activate` and publishes. Never weaken the guard that
  treats a missing `active_version_id` as "the edit is live" (loud message, exit 4), and
  never let any code path claim a preview happened without that field.
- Response validators tolerate unknown extra fields. Never add a "no extra fields" check
  to a response validator or an eval assert — the contract grows (`active_version_id` did),
  and such a check turns a server rollout into a client failure after a successful mutation.
- Section templates live outside content versions: the write does not carry them and no
  version revert touches them. The push gate refusing a workspace with modified
  `section_templates` is a safety boundary — do not remove it. Markup previews go through
  a new template plus a `template_id` rebind, never through editing a live template.
- `POST /constructor-api/migrate` may only ever be sent with strictly empty settings
  (`schema widget-version`). On real data migrate overwrites content with placeholders —
  never add a code path that sends live settings to it.
- Template update/delete, media deletion, page attachment, and mass-destructive operations
  are unavailable. Refuse them locally, without an API call.
- Pages have no endpoint of their own: they travel only inside the full-replace body.
  The page rules in `scripts/kitlib/pages.py` (canonical patterns, alias catalog, variant
  derivation, hidden-status subset, protected default pages) mirror the server validators —
  keep them in sync with the backend and never weaken the protected-page removal refusal.
  `variant_type` and `exact` are derived values: never add a code path that lets a caller
  set them by hand. Assigning productCard/categoryAndCollection variants to specific
  products or collections is a separate entity and stays out of this skill.
- There is **no DELETE route** for section templates. Anything created against a live
  contour must be recorded in artifacts and removed by hand through the B2B cabinet — say
  so instead of implying cleanup happened.
- Creating a `SectionTemplate` does not attach it to a page and does not make it visible on
  the storefront. Never report a create as visible.
- Upload only a local file path supplied by the user. Never fetch media from a URL.
- Never print, log, commit, or pass `$KIT_TOKEN` as a process argument. The same applies to
  eval artifacts: they carry sanitized metadata, never bearer values, multipart bytes, or
  full constructor payloads.
- `token web` (`scripts/kitlib/tokenweb.py`) is the only server this skill ever runs, and it
  exists so a token never has to travel through a chat. Every one of its guards is load-bearing:
  loopback bind, loopback peer **and** `Host` header (that pair is what stops DNS rebinding),
  a one-time URL secret compared with `compare_digest` and capped at a wrong-guess count,
  `log_message` silenced, the token accepted only in a POST body, a body-size cap, a CSP with
  no external origin, one successful save then exit, and a deadline. Do not bind another
  interface, do not accept the token in a query string, do not keep the server alive after a
  save, and do not add a route that reads the token back out. The page never calls the API:
  it validates through the same `_token_problem` rule as the CLI and writes the same 600 file.
- The reachability preflight (`unreachable_reason`) is part of that safety, not a convenience.
  A hosted client that prints a loopback link the user cannot open teaches them to look for
  another way to hand over a secret, and the obvious other way is the chat. Refuse instead, and
  say where the token belongs in that environment. The fix for «the page did not open» is never
  binding a non-loopback address or proxying the page — `--force` covers the only legitimate
  case, a port the user forwards themselves.
- That refusal must always name a way forward. A hosted session has two routes: the session's
  environment or secret settings, and the token sent in the conversation as text or file. Keep
  both, in that order, and keep the revoke reminder on the second — its only cost is that the
  value stays in the history. A dead end is not the safe choice; the expected user is a merchant,
  not a developer, and one who cannot proceed invents something worse.
- The built-in base URL is production. Never hardcode a non-production host; other contours
  are reachable only through `KIT_API_BASE_URL` / `--base-url`, and the storefront BFF only
  through `KIT_BFF_BASE_URL` / `--bff-url` (unauthenticated — never send the token to it).
- Telemetry is headers and nothing else — no event endpoint, no install report,
  no background call. Do not add one back.
- Self-update (`scripts/kitlib/selfupdate.py`) is the **one** exception to «never add
  a transport»: it reads `X-Skill-Version` off responses the client already receives and,
  when this copy is older, downloads `github.com/yandex/kit-skills`. That download is the only
  connection in this skill that does not go to the Kit API or the storefront BFF, and it
  carries nothing about the user. Do not widen it: no other host, no manifest endpoint, no
  request whose only purpose is to ask about versions. Its guards are load-bearing —
  `SKILL_VERSION` compared numerically (an unparsable version means «not newer»), a
  stop-latch that never retries the same target twice, archive members checked for
  traversal and links before extraction (the `filter=` argument of `extractall` needs 3.12
  and the floor here is 3.9), installation handed to a **separate process** because this
  one is running from the files being replaced, and a hard skip when the skill directory
  is a symlink or sits inside a checkout. It runs after the command and may never change
  its exit code: a working command must not start failing because a mirror was down.
- Every request identifies the skill. `X-Skill` travels on both transports —
  `request_json` for the API and `http_json` for the BFF — and `--skill-prompt` /
  `--skill-session-id` are threaded into every `request_json` call site. A new
  command that skips them still works, which is exactly why the suite reads this
  file's call sites instead of trusting the commands it knows. The header names,
  the 1000-character limit and the `YANDEX_KIT_SKILLS_TELEMETRY_DISABLED` opt-out
  (which drops every one of them) live once, in `kitlib.common`.

## Portability

- Python 3 stdlib-only and path-portable (`pathlib`), **floor is Python 3.9** — that is the
  system `python3` on macOS, which is what a colleague running this skill actually gets.
- No PEP 604 (`X | None`) in annotations that are evaluated at runtime unless the module has
  `from __future__ import annotations`.
- Never hand a raw upstream timestamp to `datetime.fromisoformat`. The backend serializes
  time without padding the fraction, so real responses carry 1-9 fractional digits and an
  explicit offset; `fromisoformat` accepts arbitrary digits only from 3.11 onwards. Normalize
  first — see `_normalize_iso8601` in `./scripts/kit.py`; the evaluation suite keeps an
  independent twin of it, deliberately not shared, so a defect in one is caught by the other.

## Evals

The suite for this skill lives in the internal `yandex-kit-skills-internal` package,
together with the rules that govern it (fixtures must not encode the client's own
assumptions; the two live modes stay separate; run on both 3.9 and a modern
interpreter). Behaviour changed here is not finished until that suite is updated
and green against this checkout.

## Changing behavior

When safety or contract behavior changes, update together: `./SKILL.md`,
`./references/api.md`, `./references/workflow.md`, `./scripts/kit.py` and
`./agents/openai.yaml` (kept in sync with the `SKILL.md` frontmatter), plus the
evaluation suite in the internal package.
