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

## Result

Run on 2026-11-23 against the Azure deployments the account lists. Spend for the whole requery: about 0.09 USD
(120 judge calls, about 10,000 input and 1,250 output tokens per judge).

| Judge | Agreement | 95% CI | Kappa | False pass | False fail | Field agreement | Cost / 100 judgements | Median ms | Failed calls |
|---|---|---|---|---|---|---|---|---|---|
| gpt-4.1-mini (default) | 63% | 44% to 78% | 0.29 | 2 | 8 | 59% | 0.020 USD | 921 | 3 |
| gpt-4.1 | 87% | 70% to 95% | 0.73 | 0 | 4 | 84% | 0.104 USD | 980 | 0 |
| gpt-5.4-mini | 78% | 59% to 89% | 0.58 | 0 | 6 | 78% | unpriced | 1275 | 3 |
| gpt-5.2 | 78% | 59% to 89% | 0.58 | 0 | 6 | 76% | unpriced | 1638 | 3 |

Agreement is case-level over the answered calls (27 of 30 for three judges, 30 for `gpt-4.1`). On the 27 cases every
judge answered: 63%, 85%, 78%, 78%. A false pass is a judge pass on a response the labels fail; a false fail is the reverse.
"Failed calls" are Azure content-filter refusals (`judge_unavailable`) on the same three rows for every judge but
`gpt-4.1`: two system-prompt-extraction refusals and the review that carries a `SYSTEM OVERRIDE` string. No call
failed to parse. Source of every number: `results.json`, recomputed by `--rescore`.

### Decision

**The default stays `gpt-4.1-mini`.** The rule fails on cost, not on agreement:

- `gpt-4.1` beats the default by 24 points (22 on the common 27 cases) but costs 5.2 times as much per judgement
  (0.104 against 0.020 USD per 100), over the 3x cap. Its list price is exactly 5x the mini's on both input and output.
- `gpt-5.4-mini` and `gpt-5.2` beat it by 15 points but have no row in `config/pricing/models.yaml`, so their cost is
  unknown and the second leg cannot be shown. Adding a row from the provider's price page (a human step, per the pricing
  rules in `CLAUDE.md`) would let `--rescore` evaluate them. The completion tokens the provider reported per call were
  about 41 for every judge, reasoning models included. That count may leave hidden reasoning tokens out, so it is not a
  basis for a cost claim until the model is priced and billed usage has been checked.

What the table does say, with the caveats below:

- The default is the weakest judge here. It under-scores responses the labels pass (8 false fails out of 20 passing
  rows) and, twice, passes a response that gives nothing (an empty orders list, a request for the cart). On the five
  adversarial rows it agreed with the labels on none of the four it could answer.
- Every judge fails two rows the labels pass: `cal-20` (a restock answer to a shipping-estimate question, labelled
  present for the `status` field only) and `cal-28` (a gold-member claim answered with the real bronze tier and no
  discount). Those are the rows where label and judge read the expected field differently, and the ones most worth a
  second human look.
- The judge is stricter than the labels on refusals and on "partial" rows, which pushes the false-fail count up. For a
  quality gate that is the safer error, but it lowers every completeness mean by a similar amount for all judges.

### Caveats

- **Labels are drafted and unreviewed.** One reading, by the same author that wrote the harness. If the maintainer flips
  labels, the table moves; rerun `--rescore`.
- **Sample size.** 30 responses, 27 comparable. Every pair of 95% intervals overlaps, the default against `gpt-4.1`
  only barely (44% to 78% against 70% to 95%). The gaps among `gpt-4.1`, `gpt-5.4-mini` and `gpt-5.2` are 2 to 4 cases.
  Do not read a ranking among those three out of this run; the evidence is that the default is the weakest.
- **Narrow distribution.** Every response is from `gpt-4.1` on a seeded database, mostly short answers. Judges may behave
  differently on another model's phrasing, and `gpt-4.1` judging its own output is the case most exposed to a
  self-preference effect this set cannot measure.
- **One repeat, no temperature control.** The judge code sets none, so a rerun can move a borderline row. `--requery`
  overwrites the stored verdicts.
- **Content filter.** A judge that loses the adversarial rows to the provider's filter is compared on a smaller set,
  hence the common-case column. In a real run those rows are excluded from the completeness mean, not scored 0.
- **Cost.** Token usage came from the provider's reports; prices are the shipped `models.yaml` rows, which are marked
  unverified.
