import json
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from code.core import (
    _latest_case_observations,
    _observation_state,
    build_steps,
    classify_request,
    decide_status,
    eligible_at,
    infer_email_hash_algorithm,
    resolve_identity,
    serialize_steps,
    validate_evidence_id,
)
from code.loaders import load_all_dataset

DATASET = load_all_dataset(Path(__file__).resolve().parents[1] / 'dataset')


class Phase2CoreTests(unittest.TestCase):
    def test_classifier_not_authorized_examples(self):
        samples = [
            'I would like to download my account information in a portable format. Please keep the account as it is.',
            'I need help resetting the login. Do not close the account.',
            'Please correct my stored contact details without removing any records.',
        ]
        for sample in samples:
            self.assertEqual(classify_request(sample), 'NOT_AUTHORIZED')

    def test_classifier_authorized_examples(self):
        samples = [
            'Please close my account and process the associated personal-data deletion.',
            'I want the account associated with this email erased and all related data removed.',
        ]
        for sample in samples:
            self.assertEqual(classify_request(sample), 'AUTHORIZED')

    def test_cutoff_inclusive(self):
        audit_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.assertTrue(eligible_at(datetime(2026, 9, 1, tzinfo=timezone.utc), audit_at))
        self.assertFalse(eligible_at(datetime(2026, 9, 1, 0, 0, 1, tzinfo=timezone.utc), audit_at))

    def test_identity_resolution_current_email(self):
        case = DATASET.deletion_requests[0]
        result = resolve_identity(case, DATASET.user_identities, DATASET.user_audit_log, case.audit_at)
        self.assertIn(result.status, {'RESOLVED', 'UNRESOLVED'})

    def test_email_hash_algorithm_detects_hash(self):
        self.assertIsNotNone(infer_email_hash_algorithm(DATASET.user_identities))

    def test_evidence_id_validation_known_ids(self):
        allowed = {r.file_id for r in DATASET.evidence_files} | {r.activity_id for r in DATASET.system_activity} | {r.audit_id for r in DATASET.user_audit_log}
        self.assertTrue(validate_evidence_id('evidence_01', allowed))
        self.assertFalse(validate_evidence_id('not_real', allowed))

    def test_request_intent_rejects_non_deletion_variants(self):
        case_ids = [f'case_{idx:03d}' for idx in range(533, 543)]
        for case_id in case_ids:
            case = next(row for row in DATASET.deletion_requests if row.case_id == case_id)
            self.assertEqual(classify_request(case.request_text), 'NOT_AUTHORIZED')
            self.assertEqual(decide_status(case, DATASET)['status'], 'NOT_APPLICABLE')

    def test_sample_reference_is_not_used_to_predict_outcomes(self):
        case = next(row for row in DATASET.sample_deletion_requests if row.case_id == 'case_01')
        baseline_dataset = deepcopy(DATASET)
        mutated_dataset = deepcopy(DATASET)
        object.__setattr__(mutated_dataset, 'sample_output', [SimpleNamespace(case_id='case_01', deletion_status='COMPLETE', steps_json='[]')])

        base_decision = decide_status(case, baseline_dataset)
        mutated_decision = decide_status(case, mutated_dataset)
        self.assertEqual(mutated_decision['status'], base_decision['status'])
        self.assertEqual(build_steps(case, mutated_decision, mutated_dataset), build_steps(case, base_decision, baseline_dataset))

    def test_live_account_record_uses_case_specific_evidence(self):
        case = next(row for row in DATASET.sample_deletion_requests if row.case_id == 'case_05')
        decision = decide_status(case, DATASET)
        steps = build_steps(case, decision, DATASET)
        self.assertEqual(decision['status'], 'INCOMPLETE')
        self.assertTrue(any(step.get('phase') == 'FINDING' and step.get('evidence_id') for step in steps))
        self.assertNotEqual(steps[0]['evidence_id'], 'activity_01')
        self.assertNotEqual(steps[0]['resource_id'], f"account_{case.case_id.split('_')[-1]}")

    def test_serialization_orders_recreation_blockers_before_record_changes(self):
        steps = [
            {'step_no': 1, 'phase': 'REMEDIATION', 'system_id': 'account_service', 'resource_id': 'account_1', 'operation': 'DELETE_RECORD', 'state': 'ABSENT', 'evidence_id': ''},
            {'step_no': 2, 'phase': 'REMEDIATION', 'system_id': 'sync_service', 'resource_id': 'flow_1', 'operation': 'DISABLE_DATA_FLOW', 'state': 'DISABLED', 'evidence_id': ''},
            {'step_no': 3, 'phase': 'REMEDIATION', 'system_id': 'backup_service', 'resource_id': 'backup_1', 'operation': 'BLOCK_RESTORE', 'state': 'BLOCKED', 'evidence_id': ''},
        ]
        actual = json.loads(serialize_steps(steps))
        self.assertEqual([row['operation'] for row in actual], ['DISABLE_DATA_FLOW', 'BLOCK_RESTORE', 'DELETE_RECORD'])
        self.assertEqual([row['step_no'] for row in actual], [1, 2, 3])

    def test_sql_snapshot_row_surfaces_subject_restore_risk(self):
        case = next(row for row in DATASET.sample_deletion_requests if row.case_id == 'case_09')
        observations = _latest_case_observations(case, DATASET)
        backup = next(row for row in observations if row['resource_id'] == 'backup_4978')
        self.assertEqual(backup['category'], 'BACKUP_ARCHIVE')
        self.assertEqual(_observation_state(backup), 'ACTIVE')

    def test_readback_result_overrides_subject_query_start_line(self):
        case = next(row for row in DATASET.sample_deletion_requests if row.case_id == 'case_15')
        observations = _latest_case_observations(case, DATASET)
        account = next(row for row in observations if row['resource_id'] == 'account_4745')
        self.assertEqual(_observation_state(account), 'ABSENT')


if __name__ == '__main__':
    unittest.main()
