# Evaluation report

## Scope and method

`python3 code/evaluation/main.py` validates the full root output against the public output contract, then scores the implementation against the 32 participant-facing examples in `dataset/sample_output.csv`. This sample score is not a measurement of accuracy on the 518 hidden evaluation cases. Hidden-answer accuracy cannot be calculated from participant-facing files and is not claimed here.

Field matches compare corresponding steps by array position and all seven required fields. Step accuracy counts a step only when all seven fields match. Sequence accuracy requires the complete status and ordered step array to match for the sample case. These metrics are intentionally reported separately from structural output validation.

## Measured results

Full evaluation output (`output.csv`):

- Contract validation: **PASS**, 518 of 518 request rows, sorted, required header, valid JSON arrays, and validator checks passed.
- Status distribution: 210 `COMPLETE`, 260 `INCOMPLETE`, 48 `NOT_APPLICABLE`.

Public sample benchmark (32 cases, 193 expected steps):

| Metric | Result |
| --- | ---: |
| Case-level deletion-status accuracy | 32/32 = **100.0%** |
| Step-level exact accuracy at expected positions | 50/193 = **25.9%** |
| Field-level match rate at expected positions | 698/1,351 = **51.7%** |
| Ordered sequence accuracy (status and all steps exact) | 1/32 = **3.1%** |

The output is structurally valid and all 32 public sample statuses match. Step quality is still materially weaker: only 50 expected steps match all fields at their corresponding positions, and only one full sample sequence matches. Passing the structural validator and public sample status benchmark does not establish correctness against hidden answers.

## Participant-created supplemental cases

These three deterministic request-classification checks are implemented in `code/evaluation/main.py` and run as part of the evaluation command:

1. **Export-only request:** “Please export my account information; do not delete anything.” Expected `NOT_AUTHORIZED`; passed.
2. **Explicit deletion request:** “Please permanently delete my account and personal data.” Expected `AUTHORIZED`; passed.
3. **Correction-only request:** “Please correct my phone number and keep the account active.” Expected `NOT_AUTHORIZED`; passed.

These are supplemental classifier checks, not substitutes for end-to-end deletion audit cases.

## Failures found and improvements made

- The final public sample run matched all 32 statuses, improved from 23/32 in the previous run.
- Record-level evidence extraction now handles JSON and executable SQLite snapshots, pairs log readback events by trace ID, filters future evidence, applies exact identifier/name boundaries, and distinguishes a record count from a query-result claim.
- SQL snapshot extraction ignores neighboring rows and maps policy categories before deriving a treatment. Text-based PDF extraction remains available when `pdftotext` is installed; raster images still require OCR.
- Validator fixes keep the 518 evaluation requests separate from public calibration rows and enforce recreation blockers before record changes. Regression coverage includes SQL restore risk, latest readback state, ordering, and dataset row selection.
- Step-level exact accuracy is 25.9%, field-level accuracy is 51.7%, and full sequence accuracy is 3.1%. Some evidence bundles do not expose a direct subject-to-resource link for every verification step; more record-level linkage and policy handling are needed.
- The evaluation runner reports only public-sample results and full-output structural validation. No hidden-set accuracy is claimed.

## Reproduction

From the repository root, run:

```bash
python3 code/evaluation/main.py
python3 -m unittest discover -s tests -q
python3 code/validate_output.py output.csv
```
