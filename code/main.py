from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.core import (
    build_case_facts,
    build_steps,
    decide_status,
    evidence_inventory,
    full_run_counts,
    infer_email_hash_algorithm,
    sample_oracle,
    serialize_steps,
    validate_output_csv,
    write_output,
)
from code.loaders import load_all_dataset


def _print_counts(dataset) -> None:
    print('DATASET_COUNTS')
    print(f"deletion_requests={len(dataset.deletion_requests)}")
    print(f"sample_deletion_requests={len(dataset.sample_deletion_requests)}")
    print(f"user_identities={len(dataset.user_identities)}")
    print(f"user_audit_log={len(dataset.user_audit_log)}")
    print(f"system_activity={len(dataset.system_activity)}")
    print(f"evidence_files={len(dataset.evidence_files)}")
    print(f"deletion_rules={len(dataset.deletion_rules)}")
    print('---')
    counts = full_run_counts(dataset)
    print('CLASSIFICATION_COUNTS', json.dumps(counts, sort_keys=True))
    print('SAMPLE_ORACLE', json.dumps(sample_oracle(dataset), sort_keys=True))
    print('EMAIL_HASH_ALGORITHM', infer_email_hash_algorithm(dataset.user_identities))
    print('EVIDENCE_INVENTORY', json.dumps(evidence_inventory(dataset), sort_keys=True))


def _write_draft_output(dataset, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in sorted(dataset.deletion_requests, key=lambda row: row.case_id):
        decision = decide_status(case, dataset)
        steps = build_steps(case, decision, dataset)
        rows.append({
            'case_id': case.case_id,
            'deletion_status': decision['status'],
            'steps_json': serialize_steps(steps),
        })
    write_output(rows, output_path)
    validate_output_csv(output_path, len(rows), dataset)
    print(f'DRAFT_OUTPUT {output_path}')
    print(f'ROWS {len(rows)}')
    print(f'STATUS_COUNTS {json.dumps({status: sum(1 for row in rows if row["deletion_status"] == status) for status in sorted({row["deletion_status"] for row in rows})}, sort_keys=True)}')


def main() -> None:
    parser = argparse.ArgumentParser(description='Ghost Data Still Exists deterministic core and draft output builder')
    parser.add_argument('--facts', help='Print a case-specific facts summary for the given case_id')
    parser.add_argument('--dataset-root', default='dataset', help='Path to the dataset directory')
    parser.add_argument('--draft-output', default=None, help='Write a staged draft output to the given path under build/; root output.csv is not written here unless explicitly requested')
    parser.add_argument('--validate-output', default=None, help='Validate an existing CSV against the contract')
    args = parser.parse_args()

    dataset = load_all_dataset(args.dataset_root)
    if args.facts:
        facts = build_case_facts(dataset, args.facts)
        print(json.dumps({
            'case_id': facts.case_id,
            'request_authorization': facts.request_authorization,
            'identity': {
                'status': facts.identity.status,
                'user_id': facts.identity.user_id,
                'email_matches': facts.identity.email_matches,
                'phone_matches': facts.identity.phone_matches,
                'reasons': facts.identity.reasons,
            },
            'eligible_evidence_ids_count': len(facts.eligible_evidence_ids),
            'unread_evidence_count': len(facts.unread_evidence),
            'rules_without_match': list(facts.rules_without_match),
            'observations_count': len(facts.observations),
        }, sort_keys=True))
        return

    if args.validate_output:
        validate_output_csv(Path(args.validate_output), len(dataset.deletion_requests), dataset)
        print(f'VALID {args.validate_output}')
        return

    if args.draft_output:
        _write_draft_output(dataset, Path(args.draft_output))
        return

    output_path = ROOT / 'output.csv'
    rows = []
    for case in sorted(dataset.deletion_requests, key=lambda row: row.case_id):
        decision = decide_status(case, dataset)
        steps = build_steps(case, decision, dataset)
        rows.append({
            'case_id': case.case_id,
            'deletion_status': decision['status'],
            'steps_json': serialize_steps(steps),
        })
    write_output(rows, output_path)
    try:
        validate_output_csv(output_path, len(rows), dataset)
    except Exception as exc:  # pragma: no cover - runtime guard
        output_path.unlink(missing_ok=True)
        raise SystemExit(f'Output validation failed: {exc}') from exc
    print(f'OUTPUT {output_path}')
    print(f'ROWS {len(rows)}')
    print(f'STATUS_COUNTS {json.dumps({status: sum(1 for row in rows if row["deletion_status"] == status) for status in sorted({row["deletion_status"] for row in rows})}, sort_keys=True)}')


if __name__ == '__main__':
    main()
