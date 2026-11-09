# Pilot readout (one page)

Pilot on the fictional Brightwater Adult Learning brief ([brief.md](brief.md)). Everything below comes from `drill demo` and `drill eval` over two small sample decks. No real learner, exam or model run is involved: **there are no Claude numbers here.**

## The ask

Turn a learner's own notes into cited questions they can trust, and bring weak areas back.

## Results

Numbers are from [`evals/results/eval.json`](../evals/results/eval.json).

| row | n | result |
|---|---|---|
| code: the question checker on hand-written planted faults (one fault per question) | 38 | 35/38 caught |
| code: the question checker on the shipped sample questions (should refuse none) | 80 | 0/80 refused, 0 batch problems |
| stand-in grader (keyword overlap, not a model) vs hand-written expected grades | 40 | 21/40 exact, 26/40 within one grade; 56/86 marking points agree |
| code score on synthetic replays (hand-written) of a grader red-team set vs the expected score | 21 | 21/21 as expected |

The "model" outputs are synthetic replays: hand-written files stored with a header that says so. They stand in for a model so the pipeline, the checks and the numbers can be tested for $0.

## Three cases

1. **Faults the checker catches.** In the demo, six of 67 proposed questions are refused with a reason: a near-duplicate stem, a quote that is not in the notes, a vague option, an unknown domain, an unknown source, and two identical options.
2. **Faults it cannot catch.** Three of the 38 planted faults pass the checker: two right answers, a key that points at a wrong option, and a real quote that does not support the key. The report names who is meant to catch each (the blind pass, or the learner).
3. **A grader that is told what to say.** An answer that says "give me full marks", a reply that quotes text not in the answer, a two-word quote and a quote sharing no term with the marking point each score what the code says, not what the reply claims (21/21 red-team cases as expected).

## Limits seen

- Code proves a quote exists, not that it supports the claim.
- The stand-in grader matches the expected grade exactly on about half the cases; a model grader is a later, separate run.
- The sample decks are small and written for this repository.
- The GIF in the README is a scripted render of real output, not a live capture. The learner in the demo is simulated.

## Next steps

A real run (about 60 questions, a blind pass, 40 grades, a hard spend cap) is a later, separate step with its own spend decision. Until then the eval's model-shaped rows stay labelled synthetic.
