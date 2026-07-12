from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tech_doc_agent.app.core.settings import get_settings
from tech_doc_agent.app.infrastructure.persistence.atomic_json import write_json_atomic
from tech_doc_agent.app.infrastructure.persistence.legacy_migration import (
    LegacyPersistenceMigrator,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or apply explicit migrations for legacy learning/memory and "
            "user-profile JSON persistence. The default mode is dry-run."
        )
    )
    parser.add_argument(
        "--data-path",
        type=Path,
        default=None,
        help="Persistence root. Defaults to the configured DATA_PATH.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up legacy sources and apply the planned migrations.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Backup root used with --apply. Defaults to DATA_PATH/migration_backups/<UTC timestamp>.",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=None,
        help="Optional JSON file for the machine-readable migration report.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data_path = args.data_path or Path(get_settings().DATA_PATH)
    report = LegacyPersistenceMigrator(data_path).run(
        apply=args.apply,
        backup_dir=args.backup_dir,
    )
    payload = report.to_payload()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.summary_output is not None:
        write_json_atomic(args.summary_output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
