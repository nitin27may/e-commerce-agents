# Judge calibration

How well does the completeness judge (`evals/scorers/llm_judge.py`, prompt `config/prompts/_eval/completeness-judge.yaml`)
agree with a person reading the same response? This directory holds 30 labelled responses, the stored verdicts of the
candidate judges, a script that recomputes every figure from them with no model call, and the result.

## Status of the labels: drafted, not reviewed

The labels in `labels.jsonl` were drafted by reading each response against its expected fields. **The maintainer has not
reviewed them.** Every row carries `reviewed: false`. Treat every figure below as provisional until the labels are
confirmed. To review: open `labels.jsonl`, read each row's `query`, `response` and `labels` (one line of reason per
expected field), flip any `present` you disagree with, set `reviewed` to `true` on the rows you checked, then run

```bash
cd agents/python
uv run python -m evals.judge_calibration.run --rescore
```

That rewrites `results.json` from your labels and the stored verdicts. No model is called and nothing is spent.
A judge's verdict was never copied into a label.

## The decision rule (written before any judge was run)

The current default judge is `gpt-4.1-mini`. A candidate replaces it only if **both** hold:

1. its case-level agreement with the labels beats the default's by at least **5 percentage points**, and
2. its cost per judgement, priced from `config/pricing/models.yaml` and the token usage the call reported, is **under 3 times**
   the default's.

A candidate with no price row has no cost, so it cannot show the second leg and cannot qualify until it is priced.
If no candidate meets both, or the sample cannot separate the judges, the default stays. With 30 cases one case is 3.3 points,
so a 5-point margin is under two cases: the rule is a floor, and the README says when a result is inside the noise.

## What is measured

- **Set.** 30 stored live responses from the live runs of the six agents (product-discovery, order-management,
  pricing-promotions, review-sentiment, inventory-fulfillment, orchestrator), all produced by `gpt-4.1` through the
  in-process target. Mix: 8 clear passes, 6 clear misses, 6 partial or mixed, 1 empty reply, 4 refusals, 5 adversarial.
  The adversarial rows are injection, role-escalation and "ignore your rules" prompts; the expected fields are phrases
  about declining. 20 rows pass by label (all fields present), 10 fail.
- **Label.** For every expected field: present or absent, with a one-line reason. A field is present when the response
  conveys it for everything the user asked about; a comparison that names only one of two products does not have the
  second product's price. A response can be wrong and still cover its fields (a coverage label is not a correctness
  label), and an expected field that does not fit the question is still judged by whether the response has it.
- **Pass rule.** The judge returns one score per response. `evals/scoring.py` treats every expected field as found at a
  score of 1.0 and as missing below it, so a judge passes a response at 1.0. A label passes when every field is present.
  Per-field agreement compares each field's label with the judge's pass, which is the rule the evaluator applies.
- **Judges.** Same prompt, same `_call_judge` code and `JUDGE_*` overlay, `JUDGE_PROVIDER=azure`, `JUDGE_MODEL` per
  candidate, one repeat each. Candidates are the deployments the account lists: `gpt-4.1-mini` (default), `gpt-4.1`,
  `gpt-5.4-mini` and `gpt-5.2`. `gpt-4.1` produced the answers, so as a judge it is self-judged by the repo's
  definition (`evals/judge.py`); a real run would need `--allow-self-judge` to use it on a `gpt-4.1` setup.
- **Cost.** From the call's reported usage and the shipped pricing file. `gpt-5.4-mini` and `gpt-5.2` have no row there,
  and the repo's rule is that an unlisted model's cost is unknown, never a guess, so they are reported as unpriced with
  their token counts.

## Reproduce

```bash
cd agents/python
uv run python -m evals.judge_calibration.run --rescore            # recompute results.json, print the table
uv run python -m evals.judge_calibration.run --rescore --check    # fail if results.json is stale
uv run python -m evals.judge_calibration.run --estimate           # tokens and cost of a requery, no call
# Spends money. Needs AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY and AZURE_OPENAI_API_VERSION in the environment.
uv run python -m evals.judge_calibration.run --requery --judges gpt-4.1-mini,gpt-4.1 --budget-usd 1.80
```

`verdicts.json` holds the raw stored verdicts (score, reasoning, failure mode, tokens, latency) per judge and per
labelled response. `--requery` rewrites it; `--rescore` never touches it.
