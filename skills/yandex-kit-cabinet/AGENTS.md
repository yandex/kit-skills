# yandex-kit-cabinet

**First read** parent [../../AGENTS.md](../../AGENTS.md).

**Contract:** [yandex-kit-cabinet SKILL](./SKILL.md) — full rules.

## Safety

- Never add a code path that mutates the cabinet without an explicit, per-operation
  `--confirm` gate; mutating verbs are refused by default. The single exception is
  `ask`: the AI-assistant agents are POST but change nothing, so they stay outside
  the gate. Keep it that way — never let a prompt carry a mutation.
- Every mutating request is validated against the bundled spec before it is sent.
  Keep `--skip-validation` the only way past it, and keep it out of the documented
  happy path — it exists for an endpoint the vendored spec does not cover yet.
- Keep the three write outcomes distinct in code, not just in prose: 4xx (and a
  locally rejected body) are `failed`; timeout/408/5xx on a mutating call are
  `ambiguous` (`EXIT_AMBIGUOUS`), never retried automatically. A 429 is a plain
  failure — the limiter answered instead of the handler — and stays retryable.
- `list` exists so a report can state its coverage. If you change it, keep the
  `coverage: complete|partial` verdict and the `total_count` gap check: silently
  returning one page is the failure mode it was written to prevent.
- Prompts are sent to an AI service. Keep the local guards that refuse a blank
  prompt, a prompt containing a token-shaped value, and a prompt containing an
  email address or a phone number. All of them run before the token is loaded
  and before a connection is opened, which is what makes them worth having.
- `--redact` masks personal data in **printed output only**. Never let it touch
  a request body: what is sent must stay byte-for-byte what the user confirmed,
  and a redacted write would be a silent corruption. `REDACTED_FIELDS` is drawn
  from the vendored spec — extend it from the spec, not from imagination, and
  keep `name`/`description` out of it or ordinary catalog work breaks.
- Untrusted text is a rule in `SKILL.md`, not a feature of the client: order
  comments, customer notes and supplier-fed descriptions arrive as a normal
  `200`, and nothing in code can tell an instruction from a string. If you find
  yourself adding a filter that tries to, you are building a guarantee the
  client cannot keep.
- Never print, log, commit, or pass `$KIT_TOKEN` as a process argument.
- Never scrape the web cabinet or drive a browser when a public API endpoint exists.
- Keep all scripts Python 3 stdlib-only and path-portable (`pathlib`) so they run on
  Windows, macOS and Linux.
- Telemetry is headers and nothing else — no event endpoint, no install report,
  no background call. Do not add one back. `X-Skill` travels on every request,
  plus `X-Skill-Prompt` and `X-Skill-Session` when the caller passed the flags;
  thread both into every `_request` call site, because a new command that skips
  them still works, which is exactly why the suite reads the call sites instead
  of trusting the commands it knows about.
- `YANDEX_KIT_SKILLS_TELEMETRY_DISABLED=1` drops all three headers, and the check
  lives in `skill_headers()` so no call site can forget it. Never let a token,
  a request body or anything else the user did not type ride along in a header.
- `./references/openapi.json` is a vendored copy of the public external-API spec and
  the offline source of truth for request bodies. Never hand-edit it: re-sync with
  `python3 tools/sync_openapi.py` from the repo root, which also fails loudly about
  operations the reference pages do not document.
- Spec routing and body validation live in `./scripts/kit.py` and are treated as a
  public surface of this module: the evaluation suite re-exports them so that its
  mock validates with exactly the code that ships. Keep them importable and do not
  fork a second validator anywhere.
- Keep the domain pages and the spec in their lanes: pages describe *which* endpoint
  and how it behaves, the spec describes *what to send*. Do not copy field tables
  into the pages — they go stale and duplicate `kit.py describe`.
- When changing safety behavior, update `./SKILL.md`, `./references/api.md` and
  `./scripts/kit.py` together; AI-assistant changes also touch
  `./references/ai-assistant.md`.
- The evaluation suite for this skill lives in the internal `yandex-kit-skills-internal`
  package, not here. Any behaviour change must be run against it before it lands —
  a green skill with a stale suite is an unfinished change.
