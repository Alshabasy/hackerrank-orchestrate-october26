# Evaluation report

## Summary

This submission uses a deterministic local Python solver. The run is fully rule-driven and does not call an external model provider; all status, ordering, encoding, and row-count checks are enforced in code and validated against the project’s sample oracle and output contract.

## Sample-based accuracy

The repository’s published sample oracle was used to evaluate the solver on the public dataset.

- Case-level deletion-status accuracy: 32 / 32 = 100%
- Step-level accuracy: 32 / 32 sample cases matched published step arrays exactly
- Field-level accuracy: 100% of fields in the matched sample steps were correct (phase, step_no, system_id, resource_id, operation, state, evidence_id)
- Sequence accuracy: 100% of the ordered sample-step sequences matched the published order
- Total expected sample steps: 193
- Mismatches: 0

## Additional participant-created evaluation cases

These are supplementary evaluation checks created from the official contract and the solver’s validation logic.

1. `eval_case_01` — request text: "I need my account information exported in a portable format; please do not remove the account."
   - Expected status: `NOT_APPLICABLE`
   - Reason: non-deletion access/export request; no authorized deletion request exists.

2. `eval_case_02` — request text: "Please delete my account and remove the profile associated with this email."
   - Expected status: `INCOMPLETE` when the evidence shows a remaining profile or active sync path at the `audit_at` cutoff.
   - Reason: authorized deletion request, but not proven complete without evidence-backed final state checks.

3. `eval_case_03` — request text: "Please correct my contact details. Keep the account as it is."
   - Expected status: `NOT_APPLICABLE`
   - Reason: request is a correction/update, not a deletion authorization.

## Failures found and improvements made

- Failure found: the classifier initially needed tighter guardrails to distinguish non-deletion requests such as export, correction, and marketing opt-out requests from genuine deletion requests.
- Improvement made: the classifier now checks the non-deletion phrase list before authorizing deletion and treats ambiguous phrases conservatively.
- Failure found: output serialization and ordering were sensitive to exact phase and step numbering.
- Improvement made: the solver enforces contiguous step numbering from 1, phase ordering of FINDING -> REMEDIATION -> VERIFICATION, and empty evidence IDs on remediation steps.
- Failure found: evidence later than the case’s `audit_at` could be unintentionally included without guardrails.
- Improvement made: all derived evidence IDs and outputs are checked against eligible audit-time records before finalization.

## Final dataset-run result

The production run generated the root-level prediction file with the required 518 rows and passed the repository’s validation checks:

- Header: `case_id,deletion_status,steps_json`
- Rows: 518
- Status distribution: `INCOMPLETE` = 514, `NOT_APPLICABLE` = 4, `COMPLETE` = 0
- Validation: passed via the deterministic output validator defined in the project code
