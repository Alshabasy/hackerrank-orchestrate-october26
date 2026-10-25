# Solver enhancement plan

## Verified output contract

- Read all 518 requests from `dataset/deletion_requests.csv` and write one row per `case_id` to root `output.csv`.
- Header is exactly `case_id,deletion_status,steps_json`; rows are sorted lexicographically by `case_id` as required by the supplied contract.
- `deletion_status` is exactly `COMPLETE`, `INCOMPLETE`, or `NOT_APPLICABLE`.
- `NOT_APPLICABLE` has `steps_json=[]`.
- Each step has exactly `step_no`, `phase`, `system_id`, `resource_id`, `operation`, `state`, and `evidence_id`; numbering starts at 1 and is contiguous.
- Phases are ordered `FINDING`, `REMEDIATION`, `VERIFICATION`. Follow the operation table ordering within each phase, then sort by `system_id` and `resource_id` where the source evidence does not establish a dependency order.
- `COMPLETE` rows contain only verification steps supported by eligible evidence. `INCOMPLETE` findings cite eligible evidence IDs; proposed remediation and future verification steps use `evidence_id=""`.
- Decision evidence must be available at or before inclusive `audit_at`. Workflow reports and activity result fields are claims, not proof of resource state.
- Apply `deletion_rules.csv`, preserve required retained records/tombstones, and handle active sync/restore risks before dependent record changes.

## Current constraints and known risks

- The repository's existing assumption register records a decision not to call external LLM APIs. The implementation will remain deterministic unless the user explicitly changes that constraint.
- `pdftotext` is available locally for PDF text. No OCR executable was found in the current environment; image evidence must not be silently treated as inspected. Probe available libraries and use a deterministic optional OCR path only if it can be made reproducible and documented.
- Public sample outputs are for evaluation only. They may not be read by prediction logic.
- Hidden canonical answers are unavailable, so report only measured public-sample accuracy and mechanical full-output validation.

## Implementation sequence and gates

1. Inventory input schemas and evidence formats; inspect representative structured, SQL, log, PDF, and image artifacts.
2. Replace heuristic state decisions with indexed identity resolution, cutoff-aware evidence observations, policy application, and explicit unknown/ambiguous handling.
3. Build every finding/remediation/verification step from the same evidence-backed resource observations, with deterministic phase/operation/system/resource ordering.
4. Add focused regressions for the discovered failure modes and public sample comparison; no sample labels enter prediction logic.
5. Run public-sample evaluation, the complete unit suite, full 518-case generation, and the independent output validator. Preserve a recoverable copy before replacing the existing output.
6. Update measured reports and package the runnable source plus README/evaluation files; inspect the archive and exclude the dataset, credentials, and caches.

## Mandatory conversation logging

Append-only root `log.txt`; each turn records the verbatim user prompt (secrets redacted), 2–5 sentence response summary, actions, and context. Each session start records one populated exact `tool=` and `model=` identifier, repository root, branch, worktree, parent agent, language, and remaining time (`not configured` per official AGENTS.md). Do not rewrite prior log entries.
