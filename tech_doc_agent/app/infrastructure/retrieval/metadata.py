"""Compatibility facade for retrieval metadata helpers.

New code should import the owning taxonomy, inference, normalization, or filters
module directly. Existing imports remain valid during the staged refactor.
"""

from tech_doc_agent.app.core.tenant import DEFAULT_NAMESPACE, DEFAULT_USER_ID
from .filters import (
    metadata_matches,
    normalize_category_filter,
    normalize_filter,
)
from .inference import infer_category, infer_tags
from .normalization import (
    METADATA_KEYS,
    normalize_chunk_metadata,
    normalize_document,
    normalize_metadata,
    normalize_tags,
)
from .taxonomy import UNCATEGORIZED

__all__ = [
    "DEFAULT_NAMESPACE",
    "DEFAULT_USER_ID",
    "METADATA_KEYS",
    "UNCATEGORIZED",
    "infer_category",
    "infer_tags",
    "metadata_matches",
    "normalize_category_filter",
    "normalize_chunk_metadata",
    "normalize_document",
    "normalize_filter",
    "normalize_metadata",
    "normalize_tags",
]
