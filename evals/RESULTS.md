# Eval results

Measured runs of `evals/cases.jsonl` against real providers. Numbers here are
from actual runs, not estimates. Re-run with:

```bash
python -m evals.run_evals --provider <p> --model <m> --variant <name>
```

Per-case rows and full transcripts land in `evals/results/<flow>/<variant>/`.

---

## llama3.1:8b, local via Ollama — 2026-10-04

Hardware: 16 GB RAM, 8 CPU cores, Intel Iris Xe integrated graphics. **CPU-only
inference** — no discrete GPU. 23 cases, 1 rep, structured-output mode.

| Flow | Metric | Score | n |
| --- | --- | --- | --- |
| **meal_macros** | **Calories within 25%** | **67%** | 9 |
| | Protein within 35% | 56% | 9 |
| | Macro split reconstructs the total | 100% | 12 |
| | Returned a named per-item breakdown | 100% | 12 |
| | Clarified exactly when warranted | 75% | 12 |
| **activity_parse** | **Burn within 40%** | **20%** | 5 |
| | Every expected exercise appears | 60% | 5 |
| | Clarified exactly when warranted | 83% | 6 |
| **day_rating** | **Rating within 2 points** | **100%** | 5 |
| | Returned a progress note | 100% | 5 |

Latency: 47–74 s mean per call, 110 s worst case. Confidence intervals are wide
(±24–43 points) at n=5–12 — these are directional, not precise.

### Reading this

**Day ratings are the standout: 5/5.** Summarising a day that is already laid
out as numbers, and judging it against stated targets, is a reasoning task the
model does well. It correctly flagged the under-eating day as a problem rather
than rewarding the large deficit.

**Meal calories are usable, protein less so.** 67% inside ±25% is enough to be
informative if you review before saving — which the UI makes you do. Protein at
56% is the weaker signal, and protein is what the app's own advice keys off.
Failures were underestimates on composed dishes (`oats-banana`,
`chicken-rice-broccoli`, `pizza-after-answer`).

**Activity calorie estimates are not usable: 20%.** The model knows the
exercises (60% found) but its MET arithmetic is wrong by large factors — it
returned 1026 kcal for a 50-minute push session against a ~230 reference. Log
workouts manually, or correct the number before saving.

**Structure was never the problem once structured output was on.** Macro
coherence and itemisation are both 100%: every response was well-formed and
internally consistent. The errors are estimation errors, not format errors.

**Clarification is given up in structured-output mode.** Constrained generation
targets one schema, so there is no choice between `ask_clarification` and the
submit tool — the model always commits and states assumptions instead. The three
deliberately ambiguous meal cases and one vague workout case are exactly the
`ask_correct` failures above. That is the intended trade-off, and the eval
measures its cost rather than hiding it.

### Why structured output is on for Ollama

Tool calling was tried first and does not work with these models. Measured over
the same clear-meal case:

| Model | Endpoint | Mode | Usable tool calls |
| --- | --- | --- | --- |
| qwen2.5:3b | `/v1/chat/completions` | tool calling | 0/3 |
| qwen2.5:3b | `/api/chat` (native) | tool calling | 0/3 |
| llama3.1:8b | `/v1/chat/completions` | tool calling | 0/2 |
| llama3.1:8b | `/api/chat` (native) | tool calling | 0/2 |
| llama3.1:8b | either | **structured output** | **2/2** |

Identical failures on both endpoints ruled out the adapter and Ollama's
translation layer: it is the models. They call the right tool and then leave the
nested `items` array empty, or omit a required field entirely — qwen returned
`arguments: {}` for a tool whose only field is required. **A tool schema is
advisory; a `response_format` schema constrains generation.** The end-to-end
smoke test went from 0/3 to 3/3 on that change alone.

A stricter prompt did not help (0/2) — this is a decoding-time constraint
problem, not a prompting problem.

### qwen2.5:3b

Not usable for this app. 0/3 on tool calling, and it consistently chose
`ask_clarification` and then failed to fill its single required field. Faster
(~30 s vs ~60 s per call) but too weak for a schema of this shape. Left
installed for comparison; not recommended.

---

## Anthropic — not yet run

No Anthropic key has been configured, so there is no paid baseline to compare
against. A full run is 23 calls, roughly $0.12 on Sonnet or $0.06 on Haiku:

```bash
python -m evals.run_evals --provider anthropic --model claude-haiku-4-5 --variant anthropic-haiku
```
