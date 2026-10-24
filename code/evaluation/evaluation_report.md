# Evaluation report

## Summary

This submission uses a deterministic local Python solver. It does not call an external model provider, and the final dataset output is generated from the repo’s rule-driven logic and validated against the production case set.

## Measured validation results

The current branch was verified with the project’s regression suite and the dataset-specific output validation path:

- Regression suite: `python3 -m unittest discover -s tests -q`
- Result: `Ran 30 tests in 6.925s` and `OK`
- Output validation: `validate_output_csv('output.csv', Path('dataset'), case_ids)`
- Result: `validation_errors= 0`
- Final artifact: [output.csv](../../output.csv)
- Final row count: 518 rows, one for each request in the evaluation set

## Case-level and step-level status

The challenge contract requires one row per case with `case_id,deletion_status,steps_json` and valid sequencing. The current solver produces the required 518-row full-dataset output and passes the project’s production-case validation logic. Because this is a deterministic local solver rather than a model-backed pipeline, the evaluation here is measured in code checks rather than token-based model inference.

## Additional participant-created evaluation cases

These supplemental checks are grounded in the official deletion contract and the repository validator:

1. `eval_case_01` — non-deletion request for account information export with “do not remove the account” language.
   - Expected result: `NOT_APPLICABLE`
   - Reason: the request is an access/export action, not an authorized deletion request.

2. `eval_case_02` — request to delete the account and remove the profile, while the evidence still shows a live failover replica before `audit_at`.
   - Expected result: `INCOMPLETE`
   - Reason: a remaining record or active replication path prevents a complete deletion decision.

3. `eval_case_03` — correction request that asks to keep the account active and update contact details.
   - Expected result: `NOT_APPLICABLE`
   - Reason: the request is not an authorization to delete personal data.

## Failures found and improvements made

- Failure found: the decision engine could incorrectly treat a live replica as if the account were already gone when the evidence matched the subject by email alone.
- Improvement made: subject matching now uses case-specific identity + historical-email + resource evidence tied to the relevant `audit_at` cutoff before treating a record as absent.
- Failure found: post-audit evidence IDs were not being rejected by the validator in the final pass.
- Improvement made: the validator now rejects evidence timestamps after the case’s `audit_at` and enforces the phase ordering and empty-evidence rules required by the contract.
- Failure found: sample-only leakage could contaminate status decisions.
- Improvement made: the solver avoids user-supplied sample output as an oracle and relies on participant-visible evidence and rules.

## Final result

The repository’s current deterministic solver is in a verified state for the production dataset. The final root-level output file is present, contains 518 valid rows, and passes the project-confirmed validation check with zero errors.
