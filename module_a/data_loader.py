"""Module A: Data Loader for Hardware Titles.

Utilities to load titles from CSV files and create/search semantic indexes.
Supports incremental addition of new titles for enterprise extensibility.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Optional

from .title_semantic_searcher import (
    TitleSemanticSearcher,
    create_searcher_from_csv,
)


def load_titles_from_csv(
    csv_path: Path,
    title_column: str = "name",
) -> List[str]:
    """Extract titles from a CSV file.

    Args:
        csv_path: Path to CSV file.
        title_column: Name of column containing titles (default: 'name').

    Returns:
        List of title strings (stripped, non-empty).
    """
    titles = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row.get(title_column, "").strip()
            if title:
                titles.append(title)
    return titles


def load_cpu_titles(csv_path: Path) -> List[str]:
    """Load CPU titles from the CPU specs CSV.

    Uses the 'name' column from cpu_specs_complete_new.csv.
    """
    return load_titles_from_csv(csv_path, title_column="name")


def load_gpu_titles(csv_path: Path) -> List[str]:
    """Load GPU titles from the GPU specs CSV.

    Uses the 'name' column from gpu_specs_sanitized.csv.
    For GPUs, 'name' is the model name (e.g., 'RTX 3090').
    """
    return load_titles_from_csv(csv_path, title_column="name")


def create_cpu_searcher(
    csv_path: Path,
    cache_dir: Optional[Path] = None,
) -> TitleSemanticSearcher:
    """Create a semantic searcher for CPU titles.

    Args:
        csv_path: Path to cpu_specs_complete_new.csv.
        cache_dir: Optional directory for model/index caching.

    Returns:
        TitleSemanticSearcher with CPU titles indexed.
    """
    return create_searcher_from_csv(csv_path, title_column="name", cache_dir=cache_dir)


def create_gpu_searcher(
    csv_path: Path,
    cache_dir: Optional[Path] = None,
) -> TitleSemanticSearcher:
    """Create a semantic searcher for GPU titles.

    Args:
        csv_path: Path to gpu_specs_sanitized.csv.
        cache_dir: Optional directory for model/index caching.

    Returns:
        TitleSemanticSearcher with GPU titles indexed.
    """
    return create_searcher_from_csv(csv_path, title_column="name", cache_dir=cache_dir)


def create_combined_searcher(
    cpu_csv_path: Path,
    gpu_csv_path: Path,
    cache_dir: Optional[Path] = None,
) -> TitleSemanticSearcher:
    """Create a semantic searcher for both CPU and GPU titles.

    Args:
        cpu_csv_path: Path to cpu_specs_complete_new.csv.
        gpu_csv_path: Path to gpu_specs_sanitized.csv.
        cache_dir: Optional directory for model/index caching.

    Returns:
        TitleSemanticSearcher with both CPU and GPU titles indexed.
    """
    cpu_titles = load_cpu_titles(cpu_csv_path)
    gpu_titles = load_gpu_titles(gpu_csv_path)

    searcher = TitleSemanticSearcher(cache_dir=cache_dir)
    searcher.build_index(cpu_titles + gpu_titles)
    return searcher


class SearcherManager:
    """Manages lifecycle of multiple TitleSemanticSearcher instances.

    Provides a single interface for creating, reloading, and incrementally
    updating search indexes for CPU, GPU, or combined titles.
    """

    def __init__(
        self,
        cpu_csv_path: Path,
        gpu_csv_path: Path,
        cache_dir: Optional[Path] = None,
    ) -> None:
        """Initialize the manager with CSV paths.

        Args:
            cpu_csv_path: Path to CPU specs CSV.
            gpu_csv_path: Path to GPU specs CSV.
            cache_dir: Optional directory for caching.
        """
        self.cpu_csv_path = Path(cpu_csv_path)
        self.gpu_csv_path = Path(gpu_csv_path)
        self.cache_dir = cache_dir

        self._cpu_searcher: Optional[TitleSemanticSearcher] = None
        self._gpu_searcher: Optional[TitleSemanticSearcher] = None
        self._combined_searcher: Optional[TitleSemanticSearcher] = None

    @property
    def cpu_searcher(self) -> TitleSemanticSearcher:
        """Lazy-load CPU searcher."""
        if self._cpu_searcher is None:
            self._cpu_searcher = create_cpu_searcher(self.cpu_csv_path, self.cache_dir)
        return self._cpu_searcher

    @property
    def gpu_searcher(self) -> TitleSemanticSearcher:
        """Lazy-load GPU searcher."""
        if self._gpu_searcher is None:
            self._gpu_searcher = create_gpu_searcher(self.gpu_csv_path, self.cache_dir)
        return self._gpu_searcher

    @property
    def combined_searcher(self) -> TitleSemanticSearcher:
        """Lazy-load combined CPU+GPU searcher."""
        if self._combined_searcher is None:
            self._combined_searcher = create_combined_searcher(
                self.cpu_csv_path, self.gpu_csv_path, self.cache_dir
            )
        return self._combined_searcher

    def add_cpu_titles(self, titles: List[str]) -> None:
        """Incrementally add new CPU titles to both CPU and combined indexes.

        Args:
            titles: List of new CPU title strings.
        """
        self.cpu_searcher.add_titles(titles)
        if self._combined_searcher is not None:
            self._combined_searcher.add_titles(titles)

    def add_gpu_titles(self, titles: List[str]) -> None:
        """Incrementally add new GPU titles to both GPU and combined indexes.

        Args:
            titles: List of new GPU title strings.
        """
        self.gpu_searcher.add_titles(titles)
        if self._combined_searcher is not None:
            self._combined_searcher.add_titles(titles)

    def reload_all(self) -> None:
        """Force reload all indexes from CSV files.

        Call after external updates to CSV files.
        """
        self._cpu_searcher = None
        self._gpu_searcher = None
        self._combined_searcher = None
        # Trigger lazy reload
        _ = self.combined_searcher

    def save_indexes(self, base_path: Path) -> None:
        """Save all searcher indexes to disk.

        Args:
            base_path: Base directory (creates combined.pkl, cpu.pkl, gpu.pkl).
        """
        base = Path(base_path)
        base.parent.mkdir(parents=True, exist_ok=True)

        if self._combined_searcher is not None:
            self._combined_searcher.save_index(base / "combined")

        if self._cpu_searcher is not None:
            self._cpu_searcher.save_index(base / "cpu")

        if self._gpu_searcher is not None:
            self._gpu_searcher.save_index(base / "gpu")

    @classmethod
    def load_indexes(
        cls,
        cpu_csv_path: Path,
        gpu_csv_path: Path,
        index_base_path: Path,
        cache_dir: Optional[Path] = None,
    ) -> "SearcherManager":
        """Load pre-built indexes from disk.

        Args:
            cpu_csv_path: Path to CPU specs CSV (for validation).
            gpu_csv_path: Path to GPU specs CSV (for validation).
            index_base_path: Path containing combined.pkl, cpu.pkl, gpu.pkl.
            cache_dir: Optional cache directory.

        Returns:
            SearcherManager with loaded indexes.
        """
        manager = cls(cpu_csv_path, gpu_csv_path, cache_dir)

        # Try loading pre-built indexes
        combined_pkl = index_base_path / "combined.pkl"
        cpu_pkl = index_base_path / "cpu.pkl"
        gpu_pkl = index_base_path / "gpu.pkl"

        if combined_pkl.exists():
            manager._combined_searcher = TitleSemanticSearcher.load_index(
                combined_pkl, cache_dir
            )

        if cpu_pkl.exists():
            manager._cpu_searcher = TitleSemanticSearcher.load_index(cpu_pkl, cache_dir)

        if gpu_pkl.exists():
            manager._gpu_searcher = TitleSemanticSearcher.load_index(gpu_pkl, cache_dir)

        # Validate against CSVs if indexes missing
        _ = manager.combined_searcher

        return manager