import csv
import json
import os
import tempfile
import unittest
from pathlib import Path

from code.validate_output import validate_output_csv

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'dataset'


def _valid_rows():
    rows = [
        {'case_id': 'case_01', 'deletion_status': 'COMPLETE', 'steps_json': '[{"step_no":1,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_001","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":"evidence_54"}]'},
        {'case_id': 'case_02', 'deletion_status': 'NOT_APPLICABLE', 'steps_json': '[]'},
        {'case_id': 'case_03', 'deletion_status': 'INCOMPLETE', 'steps_json': '[{"step_no":1,"phase":"FINDING","system_id":"account_service","resource_id":"account_003","operation":"REPORT_REMAINING_RECORD","state":"PRESENT","evidence_id":"evidence_54"},{"step_no":2,"phase":"REMEDIATION","system_id":"account_service","resource_id":"account_003","operation":"DELETE_RECORD","state":"ABSENT","evidence_id":""},{"step_no":3,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_003","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":""}]'},
    ]
    return rows


class ValidatorMutationTests(unittest.TestCase):
    def _write_valid_file(self, rows):
        tmp = tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8', newline='')
        writer = csv.writer(tmp)
        writer.writerow(['case_id', 'deletion_status', 'steps_json'])
        for row in rows:
            writer.writerow([row['case_id'], row['deletion_status'], row['steps_json']])
        tmp.close()
        return tmp.name

    def _run_validation(self, rows, expected_ids=None):
        expected_ids = expected_ids or ['case_01', 'case_02', 'case_03']
        path = self._write_valid_file(rows)
        try:
            return validate_output_csv(path, DATASET, expected_ids)
        finally:
            os.unlink(path)

    def test_validator_rejects_bad_header(self):
        path = tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8', newline='')
        try:
            writer = csv.writer(path)
            writer.writerow(['case_id', 'deletion_status', 'wrong'])
            writer.writerow(['case_01', 'COMPLETE', '[{"step_no":1,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_001","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":"evidence_54"}]'])
            path.close()
            errs = validate_output_csv(path.name, DATASET, ['case_01'])
            self.assertTrue(any('header mismatch' in e for e in errs))
        finally:
            os.unlink(path.name)

    def test_validator_rejects_missing_row(self):
        rows = _valid_rows()[:-1]
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('missing case IDs' in e or 'row count mismatch' in e for e in errs))

    def test_validator_rejects_duplicate_row(self):
        rows = _valid_rows() + [{'case_id': 'case_01', 'deletion_status': 'COMPLETE', 'steps_json': '[]'}]
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('duplicate case IDs' in e for e in errs))

    def test_validator_rejects_unsorted_rows(self):
        rows = _valid_rows()[::-1]
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('rows are not sorted' in e for e in errs))

    def test_validator_rejects_bad_status(self):
        rows = _valid_rows()
        rows[0]['deletion_status'] = 'BAD'
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('bad status' in e for e in errs))

    def test_validator_rejects_malformed_json(self):
        rows = _valid_rows()
        rows[0]['steps_json'] = '{not valid json}'
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('malformed JSON' in e for e in errs))

    def test_validator_rejects_extra_field(self):
        rows = _valid_rows()
        rows[0]['steps_json'] = '[{"step_no":1,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_001","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":"evidence_54","extra":"nope"}]'
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('unexpected fields' in e for e in errs))

    def test_validator_rejects_missing_field(self):
        rows = _valid_rows()
        rows[0]['steps_json'] = '[{"step_no":1,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_001","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT"}]'
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('missing fields' in e for e in errs))

    def test_validator_rejects_wrong_type(self):
        rows = _valid_rows()
        rows[0]['steps_json'] = '[{"step_no":"1","phase":"VERIFICATION","system_id":"account_service","resource_id":"account_001","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":"evidence_54"}]'
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('step_no is not int' in e for e in errs))

    def test_validator_rejects_step_no_gap(self):
        rows = _valid_rows()
        rows[0]['steps_json'] = '[{"step_no":2,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_001","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":"evidence_54"}]'
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('step_no values are not contiguous' in e for e in errs))

    def test_validator_rejects_phase_out_of_order(self):
        rows = _valid_rows()
        rows[2]['steps_json'] = '[{"step_no":1,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_003","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":""},{"step_no":2,"phase":"FINDING","system_id":"account_service","resource_id":"account_003","operation":"REPORT_REMAINING_RECORD","state":"PRESENT","evidence_id":"evidence_54"}]'
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('phase order violation' in e for e in errs))

    def test_validator_rejects_wrong_state_for_operation(self):
        rows = _valid_rows()
        rows[0]['steps_json'] = '[{"step_no":1,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_001","operation":"VERIFY_RECORD_ABSENT","state":"PRESENT","evidence_id":"evidence_54"}]'
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('does not match operation' in e for e in errs))

    def test_validator_rejects_non_empty_evidence_on_remediation(self):
        rows = _valid_rows()
        rows[2]['steps_json'] = '[{"step_no":1,"phase":"FINDING","system_id":"account_service","resource_id":"account_003","operation":"REPORT_REMAINING_RECORD","state":"PRESENT","evidence_id":"evidence_54"},{"step_no":2,"phase":"REMEDIATION","system_id":"account_service","resource_id":"account_003","operation":"DELETE_RECORD","state":"ABSENT","evidence_id":"evidence_54"},{"step_no":3,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_003","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":""}]'
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('remediation step must use empty evidence_id' in e for e in errs))

    def test_validator_rejects_nonexistent_evidence_id(self):
        rows = _valid_rows()
        rows[0]['steps_json'] = '[{"step_no":1,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_001","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":"not_real"}]'
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('not found in evidence' in e or 'not found' in e for e in errs))

    def test_validator_rejects_post_audit_evidence_id(self):
        rows = [{'case_id': 'case_33', 'deletion_status': 'COMPLETE', 'steps_json': '[{"step_no":1,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_001","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":"evidence_94"}]'}]
        errs = validate_output_csv(self._write_valid_file(rows), DATASET, ['case_33'])
        self.assertTrue(any('after audit_at' in e for e in errs))
        os.unlink(self._write_valid_file(rows))

    def test_validator_rejects_complete_with_remediation_step(self):
        rows = _valid_rows()
        rows[0]['steps_json'] = '[{"step_no":1,"phase":"REMEDIATION","system_id":"account_service","resource_id":"account_001","operation":"DELETE_RECORD","state":"ABSENT","evidence_id":""},{"step_no":2,"phase":"VERIFICATION","system_id":"account_service","resource_id":"account_001","operation":"VERIFY_RECORD_ABSENT","state":"ABSENT","evidence_id":"evidence_54"}]'
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('COMPLETE output may not contain REMEDIATION steps' in e for e in errs))

    def test_validator_rejects_non_applicable_non_empty_array(self):
        rows = _valid_rows()
        rows[1]['steps_json'] = '[{"step_no":1,"phase":"FINDING","system_id":"account_service","resource_id":"account_002","operation":"REPORT_REMAINING_RECORD","state":"PRESENT","evidence_id":"evidence_54"}]'
        errs = self._run_validation(rows, ['case_01', 'case_02', 'case_03'])
        self.assertTrue(any('NOT_APPLICABLE must use []' in e for e in errs))


if __name__ == '__main__':
    unittest.main()
