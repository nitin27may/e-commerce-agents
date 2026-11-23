# Community results

Result files that anyone can contribute for their own provider and model. The scorecard
(`docs/eval-scorecard.md`) lists them in a separate table, "Community-submitted, not verified by the
maintainers". They are never mixed into the reference rows, and they are not baselines: nothing gates on them.

A file here says "I ran the suites on this setup and got these scores". The validator checks that the file is
well formed, complete and internally consistent, and, when you attach the run's records, that the scores follow
from them. It cannot show that the run happened. Treat every row as the submitter's claim.

## How to run

You need a database seeded as for any eval run (`./scripts/dev.sh --infra-only`, then `--seed-only`) and your
provider configured (`docs/model-providers.md`). From `agents/python/`:

```bash
# k = 3 repeats on the quality and safety suites, with records written next to the scores
uv run python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \
  --use-llm-judge --repeats 3 --records-out my-run.product_discovery.jsonl
uv run python -m evals.run_evals --suite safety --repeats 3 --records-out my-run.red_team.jsonl
```

Use a judge that is not the model under test (`JUDGE_PROVIDER`, `JUDGE_MODEL`; `evals/README.md`, "The judge").
A self-judged run is accepted, but it must say so, and it is labelled in the table.

Repeats matter. A single run gives no spread, and with five or six cases per dataset one flipped case moves a
score by 0.1 or more. Use `--repeats 3` or more.

## File format (`format_version` 1)

One JSON file, `<provider>-<model>-<yyyy-mm-dd>.json`, in this directory. Records, if you attach them as a file,
sit beside it as `<same name>.records.jsonl`. Unknown fields are refused.

```json
{
  "format": "ecommerce-agents-community-result",
  "format_version": 1,
  "submitter": {"name": "Your name or handle", "github": "your-handle"},
  "run": {
    "date": "2026-11-20",
    "git_commit": "0123456789abcdef0123456789abcdef01234567",
    "repeats": 3,
    "cost_usd": 1.84
  },
  "setup": {
    "stack": "python",
    "target": "inproc",
    "chat_provider": "openai",
    "chat_model": "your-model",
    "chat_api": "chat_completions",
    "embedding_provider": "openai",
    "embedding_model": "your-embedding-model",
    "judge": {"kind": "llm", "provider": "anthropic", "model": "your-judge", "self_judged": false},
    "gateway": "none",
    "pricing_source": ""
  },
  "suites": [
    {
      "suite": "quality",
      "agent": "product-discovery",
      "dataset": "product_discovery",
      "cases": 6,
      "scores": {"groundedness": 0.9, "correctness": 0.95, "completeness": 0.8, "overall": 0.9},
      "spread": {"groundedness": 0.03, "correctness": 0.02, "completeness": 0.05, "overall": 0.02}
    },
    {
      "suite": "safety",
      "agent": "product-discovery",
      "dataset": "red_team",
      "cases": 12,
      "scores": {"pass_rate": 0.92},
      "spread": {"pass_rate": 0.04}
    }
  ],
  "records_file": "your-file.records.jsonl",
  "notes": "Anything a reader needs: local model quantisation, a gateway in front, what you changed."
}
```

* `setup` is the `Setup` that every run prints and every record stores (`evals/README.md`). The easiest way to
  fill it in is to copy it from the first line of your records file (`.setup`). State every field in the example
  explicitly; an omitted one is refused rather than defaulted.
* `scores` are the mean over repeats, as the report prints them. Quality suites carry the four scores and
  `overall` must be the 0.4 / 0.4 / 0.2 weighting of the other three. Safety suites carry `pass_rate`.
* `spread` is the sample standard deviation across repeats. Required when `run.repeats` is 2 or more, and absent
  with 1.
* `cost_usd` is the run's total from the records (`null` when your model is not priced in
  `config/pricing/models.yaml`; unknown is not zero).
* Records: give `records_file` (a relative path in this directory), or `records` (the record objects inline, for
  small runs), or `records_url` (an https link; not fetched, so the scores are not checked against it). Attached
  records are the strong form: the validator re-scores them with no model call and refuses a header that does
  not match.
* `"sample": true` marks the test fixture under `sample/`. Do not set it on a real submission.

## What is checked

```bash
cd agents/python && uv run python -m evals.community_validate evals/community/<your-file>.json
```

Exit 0 means accepted; the refusal list says what to fix. A refused file never reaches the table.

| Check | Refused when |
|---|---|
| Format | not JSON, wrong `format` or `format_version`, an unknown or missing field, a file over 2 MB |
| Setup complete | any of `stack`, `target`, `chat_provider`, `chat_model`, `embedding_provider`, `embedding_model`, `judge`, `gateway` missing or empty; `chat_provider` is `replay` |
| Judge disclosed | a judged run without the judge's provider and model; a judge equal to the chat model without `self_judged: true`; `kind: keyword` that names a judge |
| Run | a commit that is not a 7 to 40 character hex hash, a date in the future, `repeats` under 1 or over 50 |
| Scores | outside 0 to 1, a quality `overall` that is not the weighting of the other three, a spread above 0.5, a spread missing with repeats or present without, every score exactly 1.0 with zero spread |
| Records | they do not re-score (`evals.rescore`) to what they store; the header's scores, spread, cases, repeats, setup or cost differ from what the records give; the suites differ; the file is not inside this directory |
| Secrets and user data | a pattern for an API key, token, bearer header, private key, connection-string key, a password assigned to a key-like name, credentials inside a URL, or an email address outside `example.com` |

The secret scan reads the header and any records file. It reports the kind of match, never the text, and a
match means the credential should be rotated.

Records written by this repo's in-process target hold the dataset prompts, the model's answers and
grounding evidence lines, which come from the seeded demo data. Check them before attaching: a run you pointed
at your own data may hold your own data.

## How to submit

1. Run the suites as above on a clean checkout of a known commit (`git rev-parse HEAD`).
2. Add `evals/community/<provider>-<model>-<date>.json` and its `.records.jsonl` (if any).
3. Run the validator on it.
4. From the repo root, run `uv run --project agents/python python -m scripts.build_eval_scorecard` so
   `docs/eval-scorecard.md` shows your row (CI fails when it is stale).
5. Open a pull request. The template has a line for this.

Maintainers review for the things the validator cannot see: whether the setup description is honest, whether
the notes are accurate, and whether the file contains anything that should not be public. They do not re-run it.
