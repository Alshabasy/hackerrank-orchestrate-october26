import unittest
from datetime import datetime, timezone
from pathlib import Path

from code.core import classify_request, eligible_at, infer_email_hash_algorithm, resolve_identity, validate_evidence_id
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


if __name__ == '__main__':
    unittest.main()
