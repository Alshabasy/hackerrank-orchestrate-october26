from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.core import build_steps, classify_request, decide_status, validate_output_csv
from code.loaders import load_all_dataset


def _expected_fields(step: dict[str, Any]) -> dict[str, Any]:
    keys = ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id")
    return {key: step.get(key) for key in keys}


def evaluate(dataset_dir: Path, output_path: Path) -> dict[str, Any]:
    dataset = load_all_dataset(dataset_dir)
    validate_output_csv(output_path, len(dataset.deletion_requests), dataset)

    status_correct = 0
    sequence_correct = 0
    aligned_field_correct = 0
    aligned_step_exact = 0
    expected_step_count = 0
    expected_field_count = 0
    expected_by_case = {row.case_id: row for row in dataset.sample_output}
    if set(expected_by_case) != {row.case_id for row in dataset.sample_deletion_requests}:
        raise ValueError("Public sample inputs and expected outputs do not contain the same case IDs")
    for request in dataset.sample_deletion_requests:
        expected_row = expected_by_case[request.case_id]
        decision = decide_status(request, dataset)
        actual_steps = build_steps(request, decision, dataset)
        expected_steps = json.loads(expected_row.steps_json)
        status_correct += decision["status"] == expected_row.deletion_status
        sequence_correct += (
            decision["status"] == expected_row.deletion_status
            and actual_steps == expected_steps
        )
        expected_step_count += len(expected_steps)
        expected_field_count += len(expected_steps) * 7
        for index, expected_step in enumerate(expected_steps):
            if index < len(actual_steps):
                actual_step = _expected_fields(actual_steps[index])
                expected_step = _expected_fields(expected_step)
                aligned_step_exact += actual_step == expected_step
                aligned_field_correct += sum(actual_step[key] == expected_step[key] for key in expected_step)

    supplemental = [
        ("export only", "Please export my account information; do not delete anything.", "NOT_AUTHORIZED"),
        ("explicit deletion", "Please permanently delete my account and personal data.", "AUTHORIZED"),
        ("correction only", "Please correct my phone number and keep the account active.", "NOT_AUTHORIZED"),
    ]
    supplemental_results = [
        {"case": label, "expected": expected, "actual": classify_request(prompt), "passed": classify_request(prompt) == expected}
        for label, prompt, expected in supplemental
    ]

    rows = len(dataset.deletion_requests)
    sample_count = len(dataset.sample_deletion_requests)
    return {
        "full_output_rows": rows,
        "full_output_contract": "PASS",
        "public_sample_cases": sample_count,
        "public_sample_status_accuracy": status_correct / sample_count if sample_count else 0.0,
        "public_sample_status_correct": status_correct,
        "public_sample_sequence_accuracy": sequence_correct / sample_count if sample_count else 0.0,
        "public_sample_sequence_exact_cases": sequence_correct,
        "public_sample_step_field_accuracy_aligned": aligned_field_correct / expected_field_count if expected_field_count else 0.0,
        "public_sample_step_fields_correct_aligned": aligned_field_correct,
        "public_sample_step_fields_expected": expected_field_count,
        "public_sample_expected_steps": expected_step_count,
        "public_sample_step_accuracy_aligned": aligned_step_exact / expected_step_count if expected_step_count else 0.0,
        "public_sample_exact_steps_at_expected_positions": aligned_step_exact,
        "supplemental_cases": supplemental_results,
    }


def main() -> None:
    result = evaluate(ROOT / "dataset", ROOT / "output.csv")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
