"""Module A: Fast Semantic Title Searcher.

Uses sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2) for
millisecond-scale semantic search over hardware titles.
"""

from __future__ import annotations

from .data_loader import (
    SearcherManager,
    create_combined_searcher,
    create_cpu_searcher,
    create_gpu_searcher,
    load_cpu_titles,
    load_gpu_titles,
    load_titles_from_csv,
)
from .title_semantic_searcher import TitleSemanticSearcher

__all__ = [
    "TitleSemanticSearcher",
    "SearcherManager",
    "create_combined_searcher",
    "create_cpu_searcher",
    "create_gpu_searcher",
    "load_titles_from_csv",
    "load_cpu_titles",
    "load_gpu_titles",
]