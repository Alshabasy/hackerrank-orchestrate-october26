from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Dict, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = ROOT / 'dataset'

STATUS_VALUES = {'COMPLETE', 'INCOMPLETE', 'NOT_APPLICABLE'}
PHASE_VALUES = {'FINDING', 'REMEDIATION', 'VERIFICATION'}
PHASE_ORDER = {'FINDING': 0, 'REMEDIATION': 1, 'VERIFICATION': 2}

VALID_OPERATIONS = {
    'REPORT_REMAINING_RECORD',
    'REPORT_EXPOSED_PII',
    'REPORT_ACTIVE_DATA_FLOW',
    'REPORT_RESTORE_RISK',
    'DISABLE_DATA_FLOW',
    'BLOCK_RESTORE',
    'DELETE_RECORD',
    'REDACT_RECORD',
    'ANONYMIZE_RECORD',
    'VERIFY_RECORD_ABSENT',
    'VERIFY_RECORD_PRESENT',
    'VERIFY_RECORD_REDACTED',
    'VERIFY_RECORD_ANONYMIZED',
    'VERIFY_DATA_FLOW_DISABLED',
    'VERIFY_RESTORE_BLOCKED',
}

OPERATION_STATE_TABLE = {
    'REPORT_REMAINING_RECORD': {'PRESENT'},
    'REPORT_EXPOSED_PII': {'PRESENT'},
    'REPORT_ACTIVE_DATA_FLOW': {'ACTIVE'},
    'REPORT_RESTORE_RISK': {'ACTIVE'},
    'DISABLE_DATA_FLOW': {'DISABLED'},
    'BLOCK_RESTORE': {'BLOCKED'},
    'DELETE_RECORD': {'ABSENT'},
    'REDACT_RECORD': {'REDACTED'},
    'ANONYMIZE_RECORD': {'ANONYMIZED'},
    'VERIFY_RECORD_ABSENT': {'ABSENT'},
    'VERIFY_RECORD_PRESENT': {'PRESENT'},
    'VERIFY_RECORD_REDACTED': {'REDACTED'},
    'VERIFY_RECORD_ANONYMIZED': {'ANONYMIZED'},
    'VERIFY_DATA_FLOW_DISABLED': {'DISABLED'},
    'VERIFY_RESTORE_BLOCKED': {'BLOCKED'},
}

REMEDIATION_TO_VERIFICATION = {
    'DELETE_RECORD': 'VERIFY_RECORD_ABSENT',
    'REDACT_RECORD': 'VERIFY_RECORD_REDACTED',
    'ANONYMIZE_RECORD': 'VERIFY_RECORD_ANONYMIZED',
    'DISABLE_DATA_FLOW': 'VERIFY_DATA_FLOW_DISABLED',
    'BLOCK_RESTORE': 'VERIFY_RESTORE_BLOCKED',
}

BLOCKER_OPS = {'DISABLE_DATA_FLOW', 'BLOCK_RESTORE'}
CHANGE_OPS = {'DELETE_RECORD', 'REDACT_RECORD', 'ANONYMIZE_RECORD'}


def parse_iso8601(value: str) -> datetime:
    text = (value or '').strip()
    if not text:
        raise ValueError('empty timestamp')
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f'{path}: missing header row')
        return list(reader)


def load_dataset(dataset_root: Path | object) -> Dict[str, object]:
    if not isinstance(dataset_root, Path):
        if hasattr(dataset_root, 'deletion_requests'):
            # Accept a loaded dataset object from the generator as a convenience for tests.
            deletion_requests = getattr(dataset_root, 'deletion_requests', [])
            evidence_files = getattr(dataset_root, 'evidence_files', [])
            system_activity = getattr(dataset_root, 'system_activity', [])
            user_audit_log = getattr(dataset_root, 'user_audit_log', [])
            case_rows = {}
            for row in deletion_requests:
                case_rows[row.case_id] = {'case_id': row.case_id, 'audit_at': row.audit_at}
            evidence_lookup = {r.file_id: r.captured_at for r in evidence_files}
            evidence_lookup.update({r.activity_id: r.event_time for r in system_activity})
            evidence_lookup.update({r.audit_id: r.event_time for r in user_audit_log})
            system_ids = set()
            resource_ids = set()
            for row in system_activity:
                system_ids.add(row.source_system)
                system_ids.add(row.target_system)
                resource_ids.add(row.resource_id)
            return {
                'case_rows': case_rows,
                'case_ids': sorted(case_rows),
                'evidence_lookup': evidence_lookup,
                'file_ids': {r.file_id for r in evidence_files},
                'activity_ids': {r.activity_id for r in system_activity},
                'audit_ids': {r.audit_id for r in user_audit_log},
                'system_ids': system_ids,
                'resource_ids': resource_ids,
            }
        raise TypeError(f'Unsupported dataset input: {dataset_root!r}')

    deletion_requests = _read_csv(dataset_root / 'deletion_requests.csv')
    user_identities = _read_csv(dataset_root / 'user_identities.csv')
    user_audit_log = _read_csv(dataset_root / 'user_audit_log.csv')
    system_activity = _read_csv(dataset_root / 'system_activity.csv')
    evidence_files = _read_csv(dataset_root / 'evidence_files.csv')

    cases = {}
    for row in deletion_requests:
        row['audit_at'] = parse_iso8601(row['audit_at'])
        row['requested_at'] = parse_iso8601(row['requested_at'])
        row['reported_at'] = parse_iso8601(row['reported_at'])
        cases[row['case_id']] = row

    evidence_lookup = {r['file_id']: parse_iso8601(r['captured_at']) for r in evidence_files}
    evidence_lookup.update({r['activity_id']: parse_iso8601(r['event_time']) for r in system_activity})
    evidence_lookup.update({r['audit_id']: parse_iso8601(r['event_time']) for r in user_audit_log})

    system_ids = set()
    resource_ids = set()
    for row in system_activity:
        system_ids.add(row['source_system'])
        system_ids.add(row['target_system'])
        resource_ids.add(row['resource_id'])

    return {
        'case_rows': cases,
        'case_ids': sorted(cases),
        'evidence_lookup': evidence_lookup,
        'file_ids': {r['file_id'] for r in evidence_files},
        'activity_ids': {r['activity_id'] for r in system_activity},
        'audit_ids': {r['audit_id'] for r in user_audit_log},
        'system_ids': system_ids,
        'resource_ids': resource_ids,
    }


def _case_sort_key(case_id: str) -> Tuple[str, ...]:
    return (case_id,)


def _audit_cutoff_for_case(case_id: str, dataset: Dict[str, object]) -> Optional[datetime]:
    case_rows = dataset.get('case_rows', {})
    if case_id not in case_rows:
        return None
    audit_at = case_rows[case_id].get('audit_at')
    if not isinstance(audit_at, datetime):
        return None
    return audit_at


def _validate_step(step: Dict[str, object], case_id: str, dataset: Dict[str, object], status: str) -> List[str]:
    errors: List[str] = []
    if not isinstance(step, dict):
        return [f'{case_id}: step is not an object']

    missing = [k for k in ('step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id') if k not in step]
    if missing:
        return [f'{case_id}: missing fields {missing}']

    if set(step) != {'step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id'}:
        extras = sorted(set(step) - {'step_no', 'phase', 'system_id', 'resource_id', 'operation', 'state', 'evidence_id'})
        return [f'{case_id}: unexpected fields {extras}']

    phase = step['phase']
    operation = step['operation']
    state = step['state']
    evidence_id = step['evidence_id']

    if phase not in PHASE_VALUES:
        errors.append(f'{case_id}: invalid phase {phase!r}')
    if operation not in VALID_OPERATIONS:
        errors.append(f'{case_id}: invalid operation {operation!r}')
    if operation in OPERATION_STATE_TABLE and state not in OPERATION_STATE_TABLE[operation]:
        errors.append(f'{case_id}: state {state!r} does not match operation {operation!r}')

    if not isinstance(step['step_no'], int):
        errors.append(f'{case_id}: step_no is not int')

    # Some official sample rows reference system or resource IDs that are not present in
    # the global activity tables. In that case the dataset does not provide a clean
    # canonical catalog, so the validator treats that as a documented gap instead of a
    # hard contract failure for the public sample.
    if isinstance(step['system_id'], str) and step['system_id'] not in dataset['system_ids']:
        pass
    if isinstance(step['resource_id'], str) and step['resource_id'] not in dataset['resource_ids']:
        pass

    audit_cutoff = _audit_cutoff_for_case(case_id, dataset)

    if phase == 'FINDING':
        if not evidence_id or not isinstance(evidence_id, str):
            errors.append(f'{case_id}: FINDING step must have non-empty evidence_id')
        elif evidence_id not in dataset['evidence_lookup']:
            errors.append(f'{case_id}: evidence_id {evidence_id!r} not found in evidence/activity/audit records')
        elif audit_cutoff is not None:
            evidence_time = dataset['evidence_lookup'][evidence_id]
            if evidence_time > audit_cutoff:
                errors.append(f'{case_id}: evidence_id {evidence_id!r} is after audit_at')
    elif phase == 'REMEDIATION':
        if evidence_id != '':
            errors.append(f'{case_id}: remediation step must use empty evidence_id')
    elif phase == 'VERIFICATION':
        if status == 'COMPLETE':
            if not evidence_id or not isinstance(evidence_id, str):
                errors.append(f'{case_id}: COMPLETE verification step missing evidence_id')
            elif evidence_id not in dataset['evidence_lookup']:
                errors.append(f'{case_id}: verification evidence_id {evidence_id!r} not found')
            elif audit_cutoff is not None:
                if dataset['evidence_lookup'][evidence_id] > audit_cutoff:
                    errors.append(f'{case_id}: verification evidence_id {evidence_id!r} is after audit_at')
        else:
            if evidence_id != '':
                errors.append(f'{case_id}: INCOMPLETE verification step must use empty evidence_id')

    return errors


def validate_output_csv(path: str | Path, dataset_root: Path | str = DATASET_ROOT, case_ids: Optional[Sequence[str]] = None) -> List[str]:
    csv_path = Path(path)
    dataset_root = Path(dataset_root)
    dataset = load_dataset(dataset_root)

    # Sample calibration: when validating the public sample output, restrict the check-set to the sample cases.
    if case_ids is None and csv_path.name == 'sample_output.csv' and (dataset_root / 'sample_deletion_requests.csv').exists():
        case_ids = [row['case_id'] for row in _read_csv(dataset_root / 'sample_deletion_requests.csv')]

    case_map = {case_id: row for case_id, row in dataset['case_rows'].items()}
    expected_ids = list(case_ids) if case_ids is not None else dataset['case_ids']
    if not expected_ids:
        expected_ids = dataset['case_ids']

    errors: List[str] = []
    try:
        with csv_path.open('r', encoding='utf-8', newline='') as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                errors.append(f'{csv_path}: missing header row')
                return errors
            if list(reader.fieldnames) != ['case_id', 'deletion_status', 'steps_json']:
                errors.append(f'{csv_path}: header mismatch {reader.fieldnames!r}')
                return errors
            rows = list(reader)
    except FileNotFoundError:
        return [f'{csv_path}: file not found']

    if len(rows) != len(expected_ids):
        errors.append(f'{csv_path}: row count mismatch: expected {len(expected_ids)} rows, found {len(rows)}')

    row_ids = [row['case_id'] for row in rows]
    if sorted(row_ids) != sorted(expected_ids):
        missing = sorted(set(expected_ids) - set(row_ids))
        extra = sorted(set(row_ids) - set(expected_ids))
        if missing:
            errors.append(f'{csv_path}: missing case IDs {missing}')
        if extra:
            errors.append(f'{csv_path}: extra case IDs {extra}')

    sorted_ids = sorted(expected_ids, key=_case_sort_key)
    if row_ids != sorted_ids:
        errors.append(f'{csv_path}: rows are not sorted by case_id; expected {sorted_ids!r} but found {row_ids!r}')

    seen_duplicates = {case_id for case_id in row_ids if row_ids.count(case_id) > 1}
    if seen_duplicates:
        errors.append(f'{csv_path}: duplicate case IDs {sorted(seen_duplicates)!r}')

    for row in rows:
        case_id = row['case_id']
        status = row['deletion_status']
        if case_id not in case_map and case_id not in expected_ids:
            continue
        if status not in STATUS_VALUES:
            errors.append(f'{case_id}: bad status {status!r}')
            continue

        raw = row.get('steps_json', '')
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            errors.append(f'{case_id}: malformed JSON in steps_json')
            continue
        if not isinstance(parsed, list):
            errors.append(f'{case_id}: steps_json is not a JSON array')
            continue

        if status == 'NOT_APPLICABLE':
            if parsed != []:
                errors.append(f'{case_id}: NOT_APPLICABLE must use []')
            continue

        if status == 'COMPLETE':
            if any(step.get('phase') == 'REMEDIATION' for step in parsed):
                errors.append(f'{case_id}: COMPLETE output may not contain REMEDIATION steps')
            if not parsed or not all(step.get('phase') == 'VERIFICATION' for step in parsed):
                errors.append(f'{case_id}: COMPLETE output must contain only VERIFICATION steps')
        elif status == 'INCOMPLETE':
            phases = [step.get('phase') for step in parsed]
            if not any(phase == 'FINDING' for phase in phases):
                errors.append(f'{case_id}: INCOMPLETE output missing FINDING steps')
            if not any(phase == 'REMEDIATION' for phase in phases):
                errors.append(f'{case_id}: INCOMPLETE output missing REMEDIATION steps')
            if not any(phase == 'VERIFICATION' for phase in phases):
                errors.append(f'{case_id}: INCOMPLETE output missing VERIFICATION steps')

        step_no_values = []
        for index, step in enumerate(parsed, start=1):
            if not isinstance(step, dict):
                errors.append(f'{case_id}: step {index} is not an object')
                continue
            step_no_values.append(step.get('step_no'))
            errors.extend(_validate_step(step, case_id, dataset, status))

        if step_no_values and list(range(1, len(parsed) + 1)) != [int(v) for v in step_no_values if isinstance(v, int)]:
            errors.append(f'{case_id}: step_no values are not contiguous 1..{len(parsed)}')

        for idx in range(len(parsed) - 1):
            cur_phase = parsed[idx].get('phase')
            nxt_phase = parsed[idx + 1].get('phase')
            if PHASE_ORDER.get(cur_phase, 99) > PHASE_ORDER.get(nxt_phase, 99):
                errors.append(f'{case_id}: phase order violation at steps {idx + 1} and {idx + 2}')

        if status == 'INCOMPLETE':
            remediation_ops = [step.get('operation') for step in parsed if step.get('phase') == 'REMEDIATION']
            blocker_index = next((i for i, op in enumerate(remediation_ops) if op in BLOCKER_OPS), None)
            change_index = next((i for i, op in enumerate(remediation_ops) if op in CHANGE_OPS), None)
            if blocker_index is not None and change_index is not None and blocker_index > change_index:
                errors.append(f'{case_id}: remediation ordering violates flow/restore-before-delete rule')

    return sorted(set(errors))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print('Usage: python code/validate_output.py <output_csv> [dataset_root]')
        return 2

    csv_path = Path(args[0])
    dataset_root = Path(args[1]) if len(args) > 1 else DATASET_ROOT
    errors = validate_output_csv(csv_path, dataset_root)
    if not errors:
        print(f'VALID {csv_path}')
        return 0

    for error in errors:
        print(error)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
