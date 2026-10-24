# Assumptions and Questions

## Output contract

- Exact header: `case_id,deletion_status,steps_json`  
  Source: `README.md` lines 67-73, `problem_statement.md` section "Required Output"; sample header in `dataset/sample_output.csv`.
- Row ordering: one row per request from `dataset/deletion_requests.csv`, sorted by `case_id` ascending. The official files specify one row per case and a sorted output; `case_id` is the unique key in the input and appears zero-padded as `case_001`, `case_002`, ... in sample files, so a lexicographic sort is equivalent to numeric sort.  
  Source: `README.md` lines 73-75, `problem_statement.md` section "Required Output"; input schema from `dataset/deletion_requests.csv`.
- Quoting/line endings: CSV is written with standard comma-delimited quoting for the `steps_json` field; sample rows use CSV quoting around JSON arrays. The grader parses JSON after reading the CSV, so field quoting is structural rather than semantic. Official task files are UTF-8 and use LF for text files.  
  Source: `dataset/sample_output.csv` (observed) and `AGENTS.md` §8.2.0 footnote about UTF-8 / `
` line endings.

## `steps_json` schema

- The `steps_json` field contains a JSON array whose element object keys are: `step_no`, `phase`, `system_id`, `resource_id`, `operation`, `state`, `evidence_id`.
- Key order is not semantically significant in JSON. Array order is significant and must follow the specified phase and operation ordering.
- Empty case representation: `[]` for `NOT_APPLICABLE` cases; no deletion or deletion verification is authorized.
- Example from official data:  
  `[{"step_no":1,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_001","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":"evidence_54"}]`  
  Source: `dataset/sample_output.csv` row for `case_01`.

## Status semantics

- `COMPLETE`: "available evidence establishes that all applicable deletion requirements are satisfied and relevant recreation/synchronization risks are addressed. A legitimately retained or properly anonymized record is not automatically a violation."  
  Source: `AGENT.md` §3, expected status semantics — complete.
- `INCOMPLETE`: "evidence demonstrates prohibited residual personal data, a failed requirement, or a relevant unresolved sync/restore path that prevents establishing completion. Include evidence-grounded findings and actionable remediation/verification steps as required by the official contract."  
  Source: `AGENT.md` §3, expected status semantics — incomplete.
- `NOT_APPLICABLE`: "A request for access, correction, export, marketing opt-out, or another non-deletion action is NOT_APPLICABLE..." plus "Use `NOT_APPLICABLE` with `steps_json=[]` when the request does not authorize deletion."  
  Source: `AGENTS.md` §6.5; `AGENTS.md` §6.3; `problem_statement.md` / required output contract.

## `audit_at` cutoff

- The decision uses only information available at or before the case's `audit_at`; evidence created later must not alter the answer.
- The input time fields are ISO-like timestamps in the dataset, and they are the cutoff comparison fields. The subset is inclusive of timestamps at `audit_at` and exclusive for later evidence.
- Source: `AGENTS.md` §6.2 and the same requirement repeated in `AGENT.md`; the sample files and all official documents treat `audit_at` as the authoritative cutoff.

## `log.txt` format

Verbatim from `AGENTS.md` §5.1 and §5.2:

```text
## [ISO-8601 TIMESTAMP] SESSION START

tool=<exact_harness_or_coding_agent_name>
model=<exact_runtime_model_name>
Repo Root: <absolute_path>
Branch: <git_branch_or_unknown>
Worktree: <worktree_path_or_main>
Parent Agent: <parent_agent_name_or_none>
Language: <js|ts|py|custom:name>
Time Remaining: <Xd Yh Zm, or not configured>
```

```text
## [ISO-8601 TIMESTAMP] <short title, max 80 chars>

User Prompt (verbatim, secrets redacted):
<exact user message, with secrets replaced by [REDACTED]>

Agent Response Summary:
<2-5 sentences: what was done, why, and any important decision>

Actions:
* <file edited / command run / tool invoked>

Context:
tool=<exact_harness_or_coding_agent_name>
model=<exact_runtime_model_name>
branch=<git_branch_or_unknown>
repo_root=<absolute_path>
worktree=<worktree_path_or_main>
parent_agent=<parent_name_or_none>
```

## Allowed-value tables

- `deletion_status`: `COMPLETE`, `INCOMPLETE`, `NOT_APPLICABLE`  
  Source: `AGENTS.md` §6.3; `README.md` lines 65-74; `dataset/sample_output.csv`.
- `phase`: `FINDING`, `REMEDIATION`, `VERIFICATION`  
  Source: `problem_statement.md` "Allowed Operations" and sample output rows.
- `operation` and `state`: `REPORT_REMAINING_RECORD` -> `PRESENT`, `REPORT_EXPOSED_PII` -> `PRESENT`, `REPORT_ACTIVE_DATA_FLOW` -> `ACTIVE`, `REPORT_RESTORE_RISK` -> `ACTIVE`, `DISABLE_DATA_FLOW` -> `DISABLED`, `BLOCK_RESTORE` -> `BLOCKED`, `DELETE_RECORD` -> `ABSENT`, `REDACT_RECORD` -> `REDACTED`, `ANONYMIZE_RECORD` -> `ANONYMIZED`, `VERIFY_RECORD_ABSENT` -> `ABSENT`, `VERIFY_RECORD_PRESENT` -> `PRESENT`, `VERIFY_RECORD_REDACTED` -> `REDACTED`, `VERIFY_RECORD_ANONYMIZED` -> `ANONYMIZED`, `VERIFY_DATA_FLOW_DISABLED` -> `DISABLED`, `VERIFY_RESTORE_BLOCKED` -> `BLOCKED`  
  Source: `problem_statement.md` table "Allowed Operations".
- `case_id` / `file_id` / `activity_id` / `audit_id` values are not a closed static enum; they are values drawn from the dataset CSVs. The scripts must validate that they exist in their respective master tables before being emitted in output.  
  Source: dataset schema inspection of `deletion_requests.csv`, `system_activity.csv`, `user_audit_log.csv`, and `evidence_files.csv`.

## Ambiguity Register

| # | question | files consulted | candidate interpretations | my proposed default | risk if wrong |
|---|---|---|---|---|---|
| 1 | What exact runtime model identifier should be logged for the harness session? | `AGENTS.md`, `.copilot/session-state/*`, `/home/amr/.copilot/config.json` | `auto`, `github_copilot_vscode_agent`, `unknown`, or a model family name | `auto` (because the session metadata explicitly stores `selectedModel: "auto"`) | Low; this is the only explicit runtime model value available for the session. |
| 2 | When sorting by `case_id`, should the code use lexical or numeric ordering? | `dataset/sample_output.csv`, `dataset/deletion_requests.csv` | lexicographic string sort or numeric sort by the suffix | Lexicographic sort by the raw `case_id` string (equivalent in the provided data because the IDs are zero-padded) | Low; the official sample IDs are consistently zero-padded and unique. |
| 3 | How strict should the output validator be about evidence IDs for `INCOMPLETE` remediation steps? | `AGENTS.md` §6.6, `problem_statement.md` "Rules" and "Allowed Operations" | empty `evidence_id` for proposed steps vs. any valid `file_id|activity_id|audit_id` | Keep proposed remediation and future verification steps with `evidence_id: ""` exactly, as required for `INCOMPLETE` cases | Medium; wrong handling would violate the contract even if the status is right. |

## Binding decisions

- 2026-10-24 — Decision #1: `case_id` sort order is numeric by the suffix, following the input-file order for `case_33` … `case_550`; do not use a string sort. IDs are not zero-padded, so string sorting would misorder `case_100` before `case_33`. Decided by: user.
- 2026-10-24 — Decision #2: approved local extraction tools are the system binaries `pdftotext` and `tesseract`, invoked via `subprocess.run([...], timeout=...)` with a fixed argument list and no shell. Setup steps must be documented in `README.md`. Decided by: user.
- 2026-10-24 — Decision #3: `AGENTS.md` upstream differs from the local copy only on line 94, where the URL begins `www.` instead of `wwww.`; do not edit the file. Report the diff if the repo is a git clone. Decided by: user.
- 2026-10-24 — Decision #4: external LLM use is not allowed. Decided by: user.
- 2026-10-24 — Decision #5: step ordering follows the official rule: phase order, then operation in the Allowed Operations table order, then `system_id` ascending, then `resource_id` ascending. Decided by: user.
- 2026-10-24 — Decision #6: sample IDs are `case_01`–`case_32`; the full set is `case_33`–`case_550`; these ranges do not overlap, and the 32 sample rows include 22 `INCOMPLETE` and 10 `COMPLETE` cases with no `NOT_APPLICABLE` rows. Decided by: user.
- 2026-10-24 — Decision #7: cutoff is inclusive for `audit_at`; evidence captured exactly at that timestamp is valid. Decided by: user.
- 2026-10-24 — Decision #8: `email_hash` = `sha256(email.strip().lower())`, and it must be reproduced for all 850 user identities. Decided by: user.
- 2026-10-24 — Decision #9: the evaluation reports live under `code/evaluation/evaluation_report.md` and `code/evaluation/usage_report.md`, and all metrics must be written in Markdown. Decided by: user.
- 2026-10-24 — Decision #10: earlier log entries with `${TS}` and prompt-contamination are left unedited because the repository requires append-only logging. Decided by: user.

## Notes

- The `sample_output.csv` file is the best public reference for the required wire format, but the official task description remains authoritative when there is any discrepancy.
- No code or output generation has been implemented yet; this file records the contract and unresolved items only.
