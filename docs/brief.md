# Brief: Brightwater Adult Learning (fictional)

Brightwater Adult Learning is a made-up community college. The college, its learners and every deck in this repository are invented or public-domain.

## What went wrong in the trial

1. Learners wrote a few dozen questions by hand and stopped; the rest of the course went undrilled.
2. Some learners pasted in exam-dump items. The college cannot allow content that came under an exam NDA.
3. A model-written question set had wrong keys and invented quotes that nobody noticed until the exam.

## What the programme lead needs

- A learner supplies their own notes and gets cited questions: every question quotes the notes it came from.
- Code checks every question and every citation; the model's output is never trusted on its own.
- A second blind pass flags keys that look ambiguous; the learner decides what enters the deck.
- Sessions bring weak domains back, weighted by the exam's domain weights.
- Free-text answers are graded against marking points, code decides the number, the learner can dispute it.
- Progress stays on the learner's machine.

## Scope

In: decks from self-written or public-domain notes; multiple choice and short free text; a command line and a local phone-width page. Out: exam-dump content, phone access over the network, accounts, a hosted service, any real exam item.

## Done when

`drill demo` runs write, check, review and three sessions with a one-line summary, `drill eval` scores the checker and the grader on hand-written sets, and `drill serve --demo` shows a question with its cited quote.
