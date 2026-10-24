# Step ordering rules

This note records the ordering rules used in the deterministic step builder and the assumptions that remain if the public sample set does not fully constrain a choice.

## Rules with explicit source

| Rule | Source | Effect |
|---|---|---|
| Findings precede remediation, then verification. | `problem_statement.md` §"Required Output" and `README.md` | Phase ordering is enforced as `FINDING`, `REMEDIATION`, `VERIFICATION`. |
| Disable data flows and block unsafe restores before changing dependent records. | `AGENTS.md` §6.5 / `problem_statement.md` §"Rules" | Flow/restore blockers are placed before delete/redact/anonymize actions when both exist in the same case. |
| Process downstream copies before their source records when the dependency requires it. | `AGENTS.md` §6.5 | Replicas and exports are ordered before primary records when the evidence shows a dependency chain. |
| For `COMPLETE` cases, only verification steps are included. | `AGENTS.md` §6.3 / `problem_statement.md` §"Required Output" | No findings or remediation steps are generated for a provably complete case. |
| For `INCOMPLETE` cases, findings cite evidence and remediation/verification use empty `evidence_id`. | `AGENTS.md` §6.6 | Proposed future actions are explicit and do not claim proof. |

## Sample-derived ordering pattern

The public sample oracle (`dataset/sample_output.csv`) was used only for evaluation, not prediction logic. It demonstrates a consistent pattern for verification-only complete cases: a single verification step or a short, ordered verification block with `phase=VERIFICATION` and no findings/remediation steps.

For cases that are not yet complete, the deterministic engine keeps phase ordering strict and places flow/restore blockers before delete/redact/anonymize steps when the same case contains both classes of actions.

## Assumption / fallback

No additional ordering signal was found in the sample output beyond the explicit phase-ordering rules and dependency-based blockers. Where both the spec and the public samples are silent, the implementation keeps the ordering deterministic by sorting within each phase by the operation table order, then by `system_id`, then by `resource_id`.
