from __future__ import annotations

from pathlib import Path

from .loaders import load_all_dataset


def main() -> None:
    dataset_dir = Path(__file__).resolve().parents[1] / 'dataset'
    data = load_all_dataset(dataset_dir)
    print(
        {
            'deletion_requests': len(data.deletion_requests),
            'deletion_rules': len(data.deletion_rules),
            'evidence_files': len(data.evidence_files),
            'system_activity': len(data.system_activity),
            'user_audit_log': len(data.user_audit_log),
            'user_identities': len(data.user_identities),
            'sample_deletion_requests': len(data.sample_deletion_requests),
            'sample_output': len(data.sample_output),
        }
    )


if __name__ == '__main__':
    main()
