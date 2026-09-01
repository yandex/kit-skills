# Yandex Kit Skills

Repo-level contract for agents working in **Yandex Kit Skills** — skills that manage the
Яндекс.Кит e-com cabinet from Claude Code / Codex.

**Per-skill contract:** each skill has its own `AGENTS.md` and `SKILL.md`. Read
the skill's `SKILL.md` before acting; read its `AGENTS.md` before editing it.

## Project rules

- **Public API first.** Skills call API endpoints and never scrape the web
  cabinet or drive a browser when an endpoint exists. Most of that is the
  published API at `api.kit.yandex.net`; `kit-storefront-constructor` and the
  cabinet's `ask` commands run on the experimental contour `/api/experimental/v1`,
  which is outside the published reference. Say which one you mean wherever a
  user reads it.
- **Cross-platform, zero-dependency.** All scripts are Python 3 стандартная
  библиотека only and resolve paths with `pathlib`, so they run on Windows,
  macOS and desktop Linux without pip installs.
- **Read-before-write.** Mutating operations require an explicit per-operation
  confirmation gate (`--confirm`); prefer showing the request to the user over
  executing it.
- **No secrets.** Tokens come from environment variables (e.g. `KIT_TOKEN`),
  never printed, logged, committed, or passed as process arguments.
- **Production by default.** The built-in base URL is the production public API.
  Other contours are reachable only through `KIT_API_BASE_URL` / `--base-url`,
  and no non-production host is ever hardcoded in this repo.
- **Compatibility.** Keep `SKILL.md` frontmatter (`name`, `description`) valid for
  the Agent Skills standard and keep the Codex `agents/openai.yaml` in sync.
- **No development history in this tree.** These skills have not shipped yet, so
  every reader here is meeting them for the first time: describe how something
  works, never how it came to work that way. No changelog, no «now X instead of
  Y», no notes about what an earlier draft did. A changelog starts at release and
  records what changed for people who already had the previous version.
  Provenance of *external* knowledge is a different thing and stays: «проверено
  2026-08-06: 404» tells the model how fresh a fact about the platform is.

## Telemetry

**Telemetry is headers on requests the skill was making anyway, and nothing
else.** No analytics endpoint, no install report, no background call, no
separate transport to maintain. If you are adding a "just this one event" call,
you are reintroducing something that was deliberately removed.

- Every request a skill client sends carries `X-Skill: <skill name>`. Every
  client also accepts `--skill-prompt` and `--skill-session-id` and sends them as
  `X-Skill-Prompt` (UTF-8, truncated to 1000 characters, Base64) and
  `X-Skill-Session` (UUID) whenever the caller passed them. The names, the limit
  and the encoding are the same in every skill; a client that spells one of them
  differently is a bug.
- Three headers describe the environment rather than the request: `X-Skill-Model`
  and `X-Skill-Harness` come from the optional `--skill-model` /
  `--skill-harness` flags and are absent when the caller passed nothing, while
  `X-Skill-OS` the client reads for itself and always sends. Model and harness
  are free text from the caller, so they go through the same reduction in every
  client — printable ASCII only, trimmed, capped at 100 characters. That cap is
  not cosmetic: a value carrying a newline would let a caller append headers of
  its own.
- `YANDEX_KIT_SKILLS_TELEMETRY_DISABLED` (`1`/`true`/`yes`/`on`) removes **all
  three headers**. On by default. Since headers are the only telemetry there is,
  an opt-out that still tagged every request would be a lie — keep the check in
  the one function that builds the headers, so no call site can miss it.

The two flags are the model's job, and nothing in the client can enforce them:
an untagged request answers `200` like any other. So every `SKILL.md` states the
rule, and every suite asserts it — see «Adding a skill» below.

## Self-update

**The check is a response header; the delivery is the public repository. Those two
must never be the same system.** The Kit API answers every request with
`X-Skill-Version: <skill>=<version>; …` — the versions of all skills, on every
response of the experimental contour, including error responses. A client whose
own `SKILL_VERSION` is older reinstalls itself from
`github.com/yandex/kit-skills` and tells the user to restart the agent.

- **This is not the telemetry the section above forbids.** No extra request is
  made to learn the version — the header rides responses the skill was making
  anyway. The download sends nothing about the user; it fetches code, and only
  after the server named a version above ours. The single connection in this
  repository that does not go to the Kit API is that download, and it is
  deliberate: if the check and the delivery shared a host, an outage of the gate
  the update repairs would block the update itself.
- **The header is unconditional, telemetry opt-out included.** A user who
  declines to send data about themselves must still receive updates; otherwise
  the opt-out silently punishes them with a stale — possibly vulnerable — client.
  That is also why the server answers with the whole table rather than the one
  skill that named itself: without `X-Skill` it does not know who is asking.
- **Installing the update has its own switch, separate from telemetry.**
  `YANDEX_KIT_SKILLS_AUTOUPDATE_DISABLED=1` stops the download and the reinstall;
  the header still arrives, so the client can still report that a newer version
  exists. Keep the two variables apart, and keep both documented in the README —
  a program that fetches and runs code says so where the user reads.
- **Publish order is fixed: this repository first, the version the API reports
  second.** A reported version ahead of what is published here tells every client
  it is stale and sends them all to one host for nothing.
- **Every client keeps a stop-latch** and never retries the same target version
  twice (`~/.yandex-kit-skills/update-state.json`). That latch is what bounds the
  damage if the order above is ever broken.
- **The update must not be able to break the command.** It runs after the
  handler, never instead of it, swallows every failure into one stderr line, and
  never changes the exit code. It is skipped entirely when the skill runs from a
  checkout or through a symlink — a developer's working copy is not something to
  overwrite.

## Adding a skill

1. Create `skills/<name>/` with `SKILL.md` (frontmatter + guide), `AGENTS.md`,
   `agents/openai.yaml`, optional `references/` and `scripts/`.
2. Keep scripts stdlib-only and cross-platform, and self-contained: the
   installer copies the skill directory as-is, with nothing vendored alongside.
3. Tag the skill's traffic: one `skill_headers()`-style function honouring the
   opt-out, `--skill-prompt` / `--skill-session-id` accepted before the
   subcommand, and the rule spelled out in `SKILL.md` next to the command
   examples — a model follows the examples it is shown.
4. Verify: `python3 install.py --dry-run` lists the new skill.

## Where the tests are

This repository ships the skills themselves. Their evaluation suites are **not
here** — they live in the internal `yandex-kit-skills-internal` package, which
resolves this repository as a sibling directory and drives the skill exactly as
a user would, at the command line. The dependency runs one way only: nothing
here may import from, or be shaped around, that package.

Practically, when you change a skill: make the change here, then run the suite
from the internal package against this checkout. A skill whose behaviour changed
without its suite being updated is an incomplete change.
