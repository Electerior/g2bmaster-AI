"""module_b/search_helpers.py
Thin wrappers around the ``TitleSemanticSearcher`` used by Module A.
These helpers expose a simple API for searching CPU or GPU titles without
exposing the underlying implementation details.
"""

from __future__ import annotations

from typing import List, Tuple

from module_a.title_semantic_searcher import TitleSemanticSearcher


# Single shared searcher instances — the embedding model loads once.
_cpu_searcher = TitleSemanticSearcher()
_gpu_searcher = TitleSemanticSearcher()
_combined_searcher = TitleSemanticSearcher()


def _initialize_searchers() -> None:
    """Load titles from CSV files into the searchers."""
    import csv
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    cpu_csv = root / "cpu_specs_complete_new.csv"
    gpu_csv = root / "gpu_specs_sanitized.csv"

    # Load CPU titles
    cpu_titles: List[str] = []
    if cpu_csv.exists():
        with open(cpu_csv, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                title = row.get("name", "").strip()
                if title:
                    cpu_titles.append(title)
    _cpu_searcher.build_index(cpu_titles)

    # Load GPU titles (combine manufacturer + name for better search)
    gpu_titles: List[str] = []
    if gpu_csv.exists():
        with open(gpu_csv, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                manufacturer = row.get("manufacturer", "").strip()
                name = row.get("name", "").strip()
                if name and manufacturer:
                    gpu_titles.append(f"{manufacturer} {name}")
                elif name:
                    gpu_titles.append(name)
    _gpu_searcher.build_index(gpu_titles)

    # Combined searcher
    _combined_searcher.build_index(cpu_titles + gpu_titles)


# Initialize on first import
_initialize_searchers()


def search_cpu_titles(query: str, top_k: int = 5) -> List[Tuple[str, float]]:
    """Search CPU titles and return ``(title, score)`` pairs.

    Forwards the request to ``TitleSemanticSearcher`` which uses a MiniLM
    embedding model. ``top_k`` defaults to 5, matching the original design.
    """
    return _cpu_searcher.search(query, top_k)


def search_gpu_titles(query: str, top_k: int = 5) -> List[Tuple[str, float]]:
    """Search GPU titles and return ``(title, score)`` pairs.

    Mirrors ``search_cpu_titles`` but is kept separate for clarity.
    """
    return _gpu_searcher.search(query, top_k)


def search_all_titles(query: str, top_k: int = 5) -> List[Tuple[str, float]]:
    """Search both CPU and GPU titles together.

    Returns unified results ranked by semantic similarity score.
    """
    return _combined_searcher.search(query, top_k)