from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tech_doc_agent.app.services.vectordb.faiss_store import FaissStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill metadata fields for existing FAISS document store JSON files."
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview metadata counts without writing files.")
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=None,
        help="Optional path to write a JSON summary of inferred metadata counts.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = FaissStore()
    loaded = store.load()
    if not loaded:
        print(f"No FAISS store found at {store.store_dir}.")
        return 1

    store.normalize_metadata()
    summary = summarize(store.documents, store.chunk_metadata)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.dry_run:
        print("Dry run only. No files were changed.")
        return 0

    store.store_dir.mkdir(parents=True, exist_ok=True)
    store.documents_path.write_text(
        json.dumps(store.documents, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    store.metadata_path.write_text(
        json.dumps(store.chunk_metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Metadata migrated in {store.store_dir}.")
    return 0


def summarize(documents: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    categories = Counter(str(doc.get("metadata", {}).get("category", "unknown")) for doc in documents)
    namespaces = Counter(str(doc.get("metadata", {}).get("namespace", "unknown")) for doc in documents)
    users = Counter(str(doc.get("metadata", {}).get("user_id", "unknown")) for doc in documents)
    return {
        "documents": len(documents),
        "chunks": len(chunks),
        "categories": dict(sorted(categories.items())),
        "namespaces": dict(sorted(namespaces.items())),
        "user_ids": dict(sorted(users.items())),
    }


if __name__ == "__main__":
    raise SystemExit(main())
