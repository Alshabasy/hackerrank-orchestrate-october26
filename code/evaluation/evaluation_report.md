# Evaluation report

## Scope and method

`python3 code/evaluation/main.py` validates the full root output against the public output contract, then scores the implementation against the 32 participant-facing examples in `dataset/sample_output.csv`. This sample score is not a measurement of accuracy on the 518 hidden evaluation cases. Hidden-answer accuracy cannot be calculated from participant-facing files and is not claimed here.

Field matches compare corresponding steps by array position and all seven required fields. Step accuracy counts a step only when all seven fields match. Sequence accuracy requires the complete status and ordered step array to match for the sample case. These metrics are intentionally reported separately from structural output validation.

## Measured results

Full evaluation output (`output.csv`):

- Contract validation: **PASS**, 518 of 518 request rows, sorted, required header, valid JSON arrays, and validator checks passed.
- Status distribution: 445 `INCOMPLETE`, 25 `COMPLETE`, 48 `NOT_APPLICABLE`.

Public sample benchmark (32 cases, 193 expected steps):

| Metric | Result |
| --- | ---: |
| Case-level deletion-status accuracy | 23/32 = **71.9%** |
| Step-level exact accuracy at expected positions | 0/193 = **0.0%** |
| Field-level match rate at expected positions | 389/1,351 = **28.8%** |
| Ordered sequence accuracy (status and all steps exact) | 0/32 = **0.0%** |

The output is structurally valid, but this sample benchmark shows that semantic and step accuracy are currently inadequate for a confident submission. Passing the structural validator does not establish correctness against hidden answers. Status accuracy decreased compared with the prior recorded run; field matching increased, while full-step and sequence accuracy remain zero.

## Participant-created supplemental cases

These three deterministic request-classification checks are implemented in `code/evaluation/main.py` and run as part of the evaluation command:

1. **Export-only request:** “Please export my account information; do not delete anything.” Expected `NOT_AUTHORIZED`; passed.
2. **Explicit deletion request:** “Please permanently delete my account and personal data.” Expected `AUTHORIZED`; passed.
3. **Correction-only request:** “Please correct my phone number and keep the account active.” Expected `NOT_AUTHORIZED`; passed.

These are supplemental classifier checks, not substitutes for end-to-end deletion audit cases.

## Failures found and improvements made

- The current public sample run found 9 incorrect statuses out of 32, compared with 7 in the prior recorded run. The current implementation should not be represented as fully semantically correct.
- No expected sample step matched exactly at its corresponding position across 193 expected steps, and no full sample sequence matched exactly.
- Corrected standalone validator defaults so the 518 submission requests are checked independently of the 32 public sample cases, while retaining sample rows for evaluation.
- Changed serialized remediation ordering to put data-flow and restore blockers before record changes, as required by the contract. Added regression tests for blocker ordering and submission row selection.
- The step builder still reduces many multi-record, policy-aware investigations to a small set of activity-derived steps. It needs a systematic, record-level evidence index and policy-driven treatment plans to raise step and sequence accuracy.
- Added deterministic PDF text extraction when `pdftotext` is installed, with a per-process cache; raster images remain unprocessed because no OCR engine is available in the runtime. Added Spanish and French deletion-intent phrases and filtered short digit fragments that can collide with unrelated identifiers.
- The evaluation runner reports only public-sample results and full-output structural validation. No hidden-set accuracy is claimed.

## Reproduction

From the repository root, run:

```bash
python3 code/evaluation/main.py
python3 -m unittest discover -s tests -q
```

The validator also accepts the generated output directly:

```bash
python3 code/validate_output.py output.csv
```
