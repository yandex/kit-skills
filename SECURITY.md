# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

Report it through GitHub's [private vulnerability reporting][gh-pvr] on this
repository, or by email to <opensource-support@yandex-team.ru> with
`kit-skills` in the subject.

Please include:

- what the issue allows an attacker to do;
- the affected file, command, or endpoint;
- steps to reproduce, ideally with the exact `kit.py` invocation;
- the skill version (commit hash) and your OS and Python version.

We aim to acknowledge a report within 3 working days and to keep you updated
until it is resolved. Please give us a reasonable period to ship a fix before
disclosing publicly.

[gh-pvr]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

## Scope

In scope:

- anything that can leak a store API token — printing it, logging it, writing it
  to an artifact, passing it as a process argument, or sending it to a host
  other than the configured API;
- a mutating operation that runs without the explicit `--confirm` gate, or a
  gate that can be bypassed;
- a path that lets a prompt or a fixture reach a file outside the workspace;
- sending a token to the unauthenticated storefront BFF.

Out of scope:

- vulnerabilities in the Яндекс.Кит service itself rather than in these skills
  (report those through <https://yandex.com/bugbounty/>);
- an agent making a poor decision that the skill correctly refused or gated;
- anything requiring the attacker to already control the machine or the token.

## What the skills fetch and run

`kit-storefront-constructor` updates itself: when a Кит API response reports a
newer published version, the client downloads this repository from GitHub and
re-runs `install.py`, replacing its own directory under the agent's skills
folder. It runs after the command, never instead of it, and reports what it did
on stderr. That download is the only connection outside the Кит API and the
storefront, and it carries nothing about the user. Set
`YANDEX_KIT_SKILLS_AUTOUPDATE_DISABLED=1` and the client only reports that a
newer version exists. No other skill fetches or executes anything.

## How these skills handle your token

- The token is read from `KIT_TOKEN`, `KIT_TOKEN_FILE`, or
  `~/.yandex-kit-skills/kit_api.token`. It is never accepted as a command-line
  argument. Every surface accepts one shape — a whitespace-free store-scoped
  `yakit_` token — which is the same shape redaction recognises, so a token the
  client holds can never be one it fails to hide.
- The token file is created with mode 600, so it is owner-only from the moment it
  exists.
- A redirect never carries the token off-host: a 3xx is followed only while it
  stays on the same origin, and refused otherwise. This is deliberate — a
  redirect handler that copies request headers onto the new request would send
  `Authorization` to whatever host `Location` names.
- **Two entry routes, chosen by where the client runs, not by the user.** When it
  runs on the user's own machine, the token is typed into a local page and never
  passes through the conversation. In a hosted session — a web session, a
  container, a cloud sandbox, SSH — that page cannot reach the user's browser, so
  the routes are the session's environment or secret settings (preferred: the
  value stays out of the transcript), and failing that the token sent in the
  conversation as text or file. The two differ in one thing: the second leaves
  the value in the conversation history. The skills say so at the time and point
  at **Настройки → API**, where the token can be revoked.
- `kit.py token web` (kit-storefront-constructor) writes that file from a page
  served on `127.0.0.1` only, reachable through a one-time secret in the URL,
  refusing any request whose peer or `Host` header is not loopback. The token is
  read from a POST body, never a URL; request logging is off; the page loads no
  external resource; the server handles one successful save and exits. It makes
  no network call — it is a local form, not a login flow. It detects a hosted
  session and refuses before binding a socket rather than printing a link that
  resolves to the user's own machine.
- It is never printed. `kit.py env` reports the token *source*, not the value.
- Eval artifacts are redacted before they are written to disk.
- The `X-Skill-Prompt` header carries the user's prompt to the Kit API when the
  agent passes one. It is Base64, which is an encoding and not a disguise, so the
  value is redacted of token-shaped strings before it is encoded — a user who is
  asked for a token sometimes pastes it into the next message. The prompt is not
  sent to the unauthenticated storefront BFF at all. Setting
  `YANDEX_KIT_SKILLS_TELEMETRY_DISABLED=1` removes the header entirely.
- Prompts sent to the AI-assistant endpoints are scanned locally for
  token-shaped values and refused before any connection is opened.
- One token grants access to exactly one store; the store is derived from the
  token itself.

If you believe a token has been exposed, revoke it in the cabinet under
**Настройки → API** and generate a new one.
