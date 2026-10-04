# Eval results

Measured runs of `evals/cases.jsonl` against real providers. Numbers here are
from actual runs, not estimates. Re-run with:

```bash
python -m evals.run_evals --provider <p> --model <m> --variant <name>
```

Per-case rows and full transcripts land in `evals/results/<flow>/<variant>/`.

---

## Head-to-head — 2026-10-04

Both runs: all 23 cases, 1 rep, same prompts, same graders.

| Flow | Metric | `gemini-3.1-flash-lite` | `llama3.1:8b` (local) |
| --- | --- | --- | --- |
| **Meal** | **Calories within 25%** | **100%** (6/6) | 67% (6/9) |
| | Protein within 35% | **100%** | 56% |
| | Macro split reconstructs total | 100% | 100% |
| | Named per-item breakdown | 100% | 100% |
| | Clarified exactly when warranted | 75% | 75% |
| **Workout** | **Burn within 40%** | **100%** (5/5) | 20% (1/5) |
| | Every expected exercise found | **100%** | 60% |
| | Clarified exactly when warranted | **100%** | 83% |
| **Day** | **Rating within 2 points** | 100% | 100% |
| | Returned a progress note | 100% | 100% |
| | **Mean latency per call** | **3.7 s** | 65.5 s |

Gemini wins on accuracy and is **~18× faster**. Caveats: n is 5–12 per metric,
so confidence intervals are wide; and the two runs differ in mechanism as well
as model — Gemini uses tool calling, Ollama uses structured output (see below),
which is itself part of why the local model never asks a question.

### Per-case

| Case | Gemini | Ollama |
| --- | --- | --- |
| act-01-push-day | PASS | fail |
| act-02-5k-run | PASS | fail |
| act-03-mixed-session | PASS | fail |
| act-04-vague-gym | asked ✓ | — |
| act-05-walk | PASS | PASS |
| act-06-yoga | PASS | fail |
| day-01 … day-05 | PASS ×5 | PASS ×5 |
| meal-01-wrap-coke | asked ✗ | PASS |
| meal-02-oats-banana | PASS | fail |
| meal-03-chicken-rice-broccoli | PASS | fail |
| meal-04-pizza-ambiguous | asked ✓ | — |
| meal-05-chicken-ambiguous | asked ✓ | — |
| meal-06-pizza-after-answer | PASS | fail |
| meal-07-black-coffee | PASS | PASS |
| meal-08-greek-yoghurt | PASS | PASS |
| meal-09-biryani-large | asked ✗ | PASS |
| meal-10-protein-shake | PASS | PASS |
| meal-11-full-english | asked ✗ | PASS |
| meal-12-leftovers-vague | asked ✓ | — |

`✓` = asking was correct, `✗` = over-asked, `—` = not scored (the model asked, so
there is no number to grade; see "Numeric metrics are left unscored on a
question" in the README).

### Reading this

**Gemini got every single case it committed to.** 100% on all three headline
metrics. There is not one wrong number in the run — its only failure mode is
*refusing to answer*.

**Its weakness is over-asking.** It asked a clarifying question on three fully
specified meals — a chicken wrap with a named drink, a 400 g biryani, a
six-component full English. All three are answerable. In the app that means an
extra round trip before you get your macros, which is mildly annoying rather
than wrong, but it is the thing to tune. The prompt's bar is "ask only if the
ambiguity would change the estimate by more than roughly 20%"; `flash-lite`
reads that more conservatively than intended.

**The local model's weakness is the opposite and worse.** It never asks (its
mode cannot), and it produces confidently wrong numbers: 1026 kcal for a
50-minute push session against a ~230 reference. A wrong number you might save
is more costly than a question you have to answer.

**Day ratings are a tie at 100%.** Judging an already-numeric day against stated
targets is apparently easy enough that an 8B local model does it as well as a
hosted one. Both correctly flagged the under-eating day as a problem rather than
rewarding the large deficit.

**Structure was never the problem for either.** Macro coherence and itemisation
are 100% across both. Every failure is an estimation error, not a format error.

### Practical catch: Gemini's free tier is 20 requests per day

Not per minute — **per day, per model**:

```
Quota exceeded for metric: generate_content_free_tier_requests,
limit: 20, model: gemini-3.8-flash
Please retry in 9h12m36s
```

A 23-case eval does not fit in one day's quota on a flagship free model. The
first attempt against `gemini-flash-latest` (which resolves to
`gemini-3.8-flash`) completed 8 of 23 and then exhausted the day. Quotas are
per-model, so the full run was done on `gemini-3.1-flash-lite`, which had
headroom.

For everyday use that cap is fine — 20 meals a day is more than anyone logs. For
running evals it is the binding constraint, so pin a lite model.

This run is also what prompted the harness fix in `run_evals.py`: rate limits
now get their own, much longer backoff schedule (20/45/75/110/150 s rather than
capping at 20 s), attempts went from 3 to 6, and `--delay` paces request starts.
The failure sidecar did its job in the meantime — the 15 lost cases went to
`errors.jsonl` rather than `results.jsonl`, so nothing was scored as a model
failure and a resume would have retried exactly those.

---

## Why Ollama uses structured output instead of tool calling

Tool calling was tried first and does not work with small local models.
Measured over the same clear-meal case:

| Model | Endpoint | Mode | Usable tool calls |
| --- | --- | --- | --- |
| qwen2.5:3b | `/v1/chat/completions` | tool calling | 0/3 |
| qwen2.5:3b | `/api/chat` (native) | tool calling | 0/3 |
| llama3.1:8b | `/v1/chat/completions` | tool calling | 0/2 |
| llama3.1:8b | `/api/chat` (native) | tool calling | 0/2 |
| llama3.1:8b | either | **structured output** | **2/2** |

Identical failures on both Ollama endpoints ruled out the adapter and the
translation layer: it is the models. They call the right tool and then leave the
nested `items` array empty, or omit a required field entirely — qwen returned
`arguments: {}` for a tool whose only field is required. **A tool schema is
advisory; a `response_format` schema constrains generation.** The end-to-end
smoke test went from 0/3 to 3/3 on that change alone. A stricter prompt did not
help (0/2) — this is a decoding-time constraint problem, not a prompting one.

The trade-off is that one schema means no choice of tool, so on the local path
the model always commits and never asks. That is why `ask_correct` is not
comparable between the two runs.

### qwen2.5:3b

Not usable for this app. 0/3 on tool calling, and it consistently chose
`ask_clarification` and then failed to fill its single required field. Faster
(~30 s vs ~60 s per call) but too weak for a schema of this shape.

---

## Hardware and cost

Local runs: 16 GB RAM, 8 CPU cores, Intel Iris Xe integrated graphics —
**CPU-only inference**, no discrete GPU. That is the main reason for the 65 s
mean latency; a machine with a GPU would be far quicker.

Anthropic has not been run — no key is configured. A full run is 23 calls,
roughly $0.12 on Sonnet or $0.06 on Haiku:

```bash
python -m evals.run_evals --provider anthropic --model claude-haiku-4-5 --variant anthropic-haiku
```
