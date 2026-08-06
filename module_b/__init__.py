"""Module B: Deep Spec Extraction Pipeline (Tier 3).

Hardware schema, LLM extraction (Ollama/LM Studio), data loading, and search helpers.

Importers:
  - MODULES_AND_TESTS/tests/unit/hardware_spec_test.py: imports load_cpu_specs, load_gpu_specs, search_cpu_titles, search_gpu_titles
  - module_b/hardware_schema.py: manages CPUInfo, GPUInfo, HardwareExtraction
  - module_b/llm_extractor.py: manages LLMExtractor
  - module_b/data_loader.py: manages load_cpu_specs, load_gpu_specs
  - module_b/search_helpers.py: manages search_cpu_titles, search_gpu_titles
"""

from __future__ import annotations

# Hardware schema
from .hardware_schema import CPUInfo, GPUInfo, HardwareExtraction, get_extraction_schema

# LLM extractor
from .llm_extractor import LLMExtractor, create_extractor

# Data loader
from .data_loader import load_cpu_specs, load_gpu_specs

# Search helpers (delegate to Module A)
from .search_helpers import search_cpu_titles, search_gpu_titles, search_all_titles

__all__ = [
    # Schema
    "CPUInfo",
    "GPUInfo",
    "HardwareExtraction",
    "get_extraction_schema",
    # Extractor
    "LLMExtractor",
    "create_extractor",
    # Data
    "load_cpu_specs",
    "load_gpu_specs",
    # Search
    "search_cpu_titles",
    "search_gpu_titles",
    "search_all_titles",
]