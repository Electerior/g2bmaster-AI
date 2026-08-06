'''module_b/data_loader.py
Utility functions to read the CPU and GPU CSV files and convert rows into the
Pydantic models defined in ``hardware_schema.py``.
All parsing uses only the Python standard library (``csv``) and Pydantic for
validation.  The ``url`` column from the source CSVs is deliberately ignored –
the user requested that URLs are not needed.
'''

from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from .hardware_schema import CPUInfo, GPUInfo


def _read_csv(path: Path) -> List[dict]:
    """Read a CSV file and return a list of row dictionaries.

    Whitespace is stripped from keys and values.  Empty rows are skipped.
    The ``url`` column, if present, is removed before the dict is returned.
    Empty string values are converted to ``None`` for Pydantic compatibility.
    """
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows: List[dict] = []
        for row in reader:
            row.pop("url", None)  # URL not needed per user request
            cleaned = {
                k.strip(): (v.strip() if v is not None else None)
                for k, v in row.items()
            }
            # Convert empty strings to None for Pydantic validation
            cleaned = {k: (v if v != "" else None) for k, v in cleaned.items()}
            if cleaned:
                rows.append(cleaned)
        return rows


def load_cpu_specs(path: Path) -> List[CPUInfo]:
    """Load CPU specifications from ``cpu_specs_complete_new.csv``.

    Returns a list of :class:`CPUInfo` objects.  Validation errors are raised
    by Pydantic, ensuring any malformed rows are caught early.
    """
    rows = _read_csv(path)
    return [CPUInfo(**row) for row in rows]


def load_gpu_specs(path: Path) -> List[GPUInfo]:
    """Load GPU specifications from ``gpu_specs_sanitized.csv``.

    Returns a list of :class:`GPUInfo` objects.  Validation errors are raised by
    Pydantic.
    """
    rows = _read_csv(path)
    return [GPUInfo(**row) for row in rows]
