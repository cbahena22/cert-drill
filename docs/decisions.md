# Decisions

Short records of why `drill` is built the way it is.

## ADR-001: The model writes, code checks, the learner decides
A model can write a question and a quote; it can also write a wrong key or a quote that is not in the notes. Every question goes through plain code (`checker.py`), and nothing enters the deck until the learner approves it. Cost: some proposed questions are refused and some good ones need a second look.

## ADR-002: Every citation is a quote that must be found in the notes
A question without a citation, or with a quote that is not in the named source (after whitespace and quote-mark normalising), is refused. This stops invented quotes. It does not prove the quote supports the key; that limit is stated in the README.

## ADR-003: "More than one right answer" goes to a blind pass
Code cannot tell that two options are both right. The model answers without the key and a disagreement is flagged to the learner. Best effort, and the learner's review stays the final check.

## ADR-004: Code computes the free-text score
The grader returns `met`, `partly` or `missed` and a quote from the answer per marking point, never a number. Code keeps a point only if the quote is in the answer, is long enough, shares two content words with the marking point and does not reuse a stretch of the answer. A "give me full marks" answer earns what its content earns. The learner can dispute with `drill dispute`.

## ADR-005: SM-2 first, weak domains first
SM-2 is small, well known and easy to test with an injected clock. A session is the due questions, then new ones from the weakest domains (rolling accuracy times weight), with a daily cap. A modern scheduler can replace it later.

## ADR-006: Synthetic replays, labelled as such
With no real run, every "model" output in the demo, tests and eval is a hand-written file whose first line says so. The loader refuses a file without the header, and every result row says "synthetic", "stand-in" or "code". A recorded run needs its own header (model and date). The real run waits for a separate decision.

## ADR-007: Local only, one token, strict page
The page binds 127.0.0.1, checks the `Host` header, needs a random per-start token on every API call, refuses cross-origin writes, caps request bodies, sends a strict CSP and sets text with `textContent`. Phone access over the network is out of this version because it needs TLS and a login.

## ADR-008: Offline by construction, nothing private in the repository
The run-time code uses the standard library only; the tests block `socket` connect and bind (except loopback tests) and delete model keys from the environment. Dependencies are hash-locked and checked with `pip-audit`; `bandit` runs over `src`. Tracked decks must be self-written or public-domain; a learner's own decks live in `decks/private/`, which is git-ignored.
