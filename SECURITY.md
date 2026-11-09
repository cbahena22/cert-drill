# Security

## Reporting a vulnerability

Use GitHub private vulnerability reporting on this repository (Security tab, "Report a vulnerability"). Please do not open a public issue for a vulnerability. Include the version, the operating system, and steps to reproduce.

This is a one-person project: expect an acknowledgement within a week.

## Scope

In scope: the study page answering a request with a wrong `Host`, no token, or a foreign `Origin`; script or markup in a question, option or answer running in the page; a deck path that reaches outside the deck folder; a deck or replay file that gets past the schema and caps; a free-text answer that earns marks the code does not award; a key, token or home folder appearing in the page, a file or a log.

Out of scope: the documented limits in the README (a quote is checked to exist, not to support the claim; the stand-in grader is keyword overlap), a hostile local user of the same OS account, and anything reachable only by exposing the server beyond 127.0.0.1 yourself.

## What the package does and does not do

- The page binds 127.0.0.1 only, checks `Host`, needs a random per-start token on every API call, refuses cross-origin POSTs and caps request bodies. The token is printed once, in the start link's fragment.
- The page has a strict Content-Security-Policy, no external asset, and sets all text with `textContent`.
- Deck text is data: length-capped, control characters refused, every path stays inside the deck folder. Sources are passed to a model only as delimited data.
- The code decides the free-text score, not the model.
- Progress is a local, git-ignored file; `drill forget` deletes it. No telemetry. No network call at run time. The optional `live` extra (a later, real model run) reads its key from the environment only; nothing is stored and nothing reaches the page.
- Dependencies are hash-locked and checked with `pip-audit`; `bandit` runs over `src`.

## Supported versions

Only the latest commit on the default branch.
