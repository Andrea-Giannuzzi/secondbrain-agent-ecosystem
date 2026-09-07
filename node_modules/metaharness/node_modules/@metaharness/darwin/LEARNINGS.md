# What the benchmarks taught us → harness defaults

Empirical findings from the full SWE-bench Lite (300) arc (official `swebench` Docker harness,
batch-verified — see `bench/results/RESULTS.md`). These are the *measured* reasons behind the
recommended harness patterns. The headline: **the harness, not the model, is the dominant lever.**

## 1. Closed-loop repair (test feedback) is the #1 lever — ~2× for free
- open-loop single-shot: **7.7%** → + closed-loop repair (run the failing tests, feed the
  traceback back, retry ≤3): **15.3%** — on the *same cheap model*, ~$0.01/instance.
- **Recommendation:** make iteration against ground-truth (compiler/tests) first-class. A model
  that can *see why it failed* beats a smarter model that can't. Prefer `retryPolicy` configs that
  consume real failure signal over blind retries.

## 2. Localization fixes retrieval, not emission — beware the "emission wall"
- LLM file-localization lifted gold-file recall **+15pp** but resolve-rate stayed flat (8.0%).
  The bottleneck was *writing a valid patch*, not *finding the file*.
- **Recommendation:** measure where you actually lose (selection vs emission) before optimizing
  retrieval. Don't assume better context = better output.

## 3. Format contract + fit-in-context unblocks weak/local models (0 → 13/25 applied)
- A small local model emitted prose summaries instead of edits until the harness (a) served enough
  context window, (b) carried the search/replace **format contract in a system message + worked
  example**, and (c) **shrank per-file context to fit the window** (truncation silently dropped the
  instruction). Apply-rate went 0 → ~50%.
- **Recommendation:** put the output-format contract in a *system* role with an example; size the
  prompt to the model's real context; never let truncation eat the instruction.

## 4. Cheap-first + cost-aware routing — 31× cheaper per resolve
- Router probe: `pareto-code`→deepseek-v4-pro resolved at **$0.21/resolve** vs `fusion`→opus-4.8 at
  **$6.57/resolve** — same task, 31× cost gap for +1 resolve.
- **Recommendation:** default to the cheapest model that clears the task; reserve frontier models for
  measured capability gaps. Track **$/resolve**, not just resolve-rate.

## 5. Barbarian & Scholar — tier the models, escalate only the residual (up to 58.3%)
- Cheap base banks the easy wins; a frontier "Scholar" escalated **only to the residual it failed**
  cracks more; a 3rd "Sage" tier escalates again. Each tier pays only for the shrinking tail. The
  batch-verified ladder on full SWE-bench Lite (300):
  - v4-pro base + repair: 88/300 = 29.3%
  - + sonnet-4 Scholar on the tail (2-tier): 121/300 = **40.3%** [34.9, 46.0], ~$0.39/inst
  - + opus-4.8 Sage on the residual (3-tier): 175/300 = **58.3%** [52.7, 63.8], ~$0.74/inst
- **Recommendation:** N-tier cheap→frontier escalation is far more cost-efficient than one strong
  model everywhere (you'd waste most of frontier spend re-solving what cheap already gets). Returns
  diminish per tier at rising $/resolve — stop where the residual's marginal cost exceeds its value.

## 6. The repair lift is model-bound below a capability floor (~14B)
- Batch-verified on full-300: repair lifts a local 14B only **+2pp (4.7% → 6.7%** [4.4, 10.1]) — and
  108/300 of its attempts were empty/invalid diffs the model couldn't emit, so the loop had nothing to
  iterate on. The *same harness* on a hosted model reaches 29.3%. The loop needs the model to
  *occasionally* produce a correct-ish patch to converge toward; below that floor, repair recovers
  little.
- **Recommendation:** don't expect harness scaffolding to rescue a model below the task's reasoning
  floor; pick the smallest model *above* it, then let the harness multiply it.

## 7. Methodology: only batch-eval on final predictions is authoritative
- In-loop "resolved" counters drifted from clean batch eval by 1.5–5× (both directions — flaky
  passes over-count; Docker-hang false-negatives under-count). Every reported number here is a
  fresh batch eval on the final saved predictions.
- **Recommendation:** never report the in-loop signal; re-evaluate the artifact you'd actually ship.

## 8. Engineering robustness (or your run lies to you)
- Concurrency clones rate-limit (6-wide anon GitHub clones → 63 fetch failures): **cap at 2–3**,
  retry-with-backoff, free each clone. One instance (`psf__requests-2317`) reliably hangs Docker
  past timeout → known-flaky exclusion (`bench/swebench/KNOWN_FLAKY.md`). Watch for wedged containers.

---

Verdict: this paradigm (localize + search/replace + repair + tiered escalation) reaches a
batch-verified **58.3%** on SWE-bench Lite via cheap-base + 3-tier frontier escalation — 7.6× the
7.7% open-loop baseline. Both within-paradigm frontiers are now exhausted: hosted (3rd-tier escalation
at steeply rising $/resolve) and local (the §6 capability floor). The 65–88% agentic-SOTA tier needs a
**multi-step autonomous agent** (read/grep/run-tests/edit/discovery loop) — an architecture change,
not more knob-tuning. That loop is now implemented + unit-tested (ADR-153: `bench/swebench/
agentic-loop.mjs` + `solve-agentic.mjs`); its at-scale number is the next arc.
