"""Module B: 하드웨어 스펙 추출.

두 방향을 한 어휘로 묶는다 — 규격서가 **요구한 것**과 데이터시트가 말하는 **제품의 값**.
같은 속성 이름을 쓰기 때문에 둘의 대조가 추론이 아니라 산수가 된다.

제목 검색 위임(`search_helpers`)은 제목 의미검색과 함께 폐지했다. 유사도는 이제
파일 내용에 대해서만 걸고, 그건 `module_a.document_index` 소관이다.

Importers:
  - module_server.py: /api/extract/specs · /api/specs/cpu|gpu
  - module_b/hardware_schema.py: manages CPUInfo, GPUInfo, HardwareExtraction, RequirementItem
  - module_b/llm_extractor.py: manages LLMExtractor
  - module_b/data_loader.py: manages load_cpu_specs, load_gpu_specs
"""

from __future__ import annotations

# Hardware schema
from .hardware_schema import (
    ATTR_UNITS,
    CATEGORY_ATTRS,
    CPUInfo,
    Constraint,
    Evidence,
    GPUInfo,
    HardwareExtraction,
    RequirementExtraction,
    RequirementItem,
    canonical_attrs,
    get_extraction_schema,
)

# LLM extractor
from .llm_extractor import LLMExtractor, create_extractor

# Data loader
from .data_loader import load_cpu_specs, load_gpu_specs

__all__ = [
    # Schema
    "ATTR_UNITS",
    "CATEGORY_ATTRS",
    "CPUInfo",
    "Constraint",
    "Evidence",
    "GPUInfo",
    "HardwareExtraction",
    "RequirementExtraction",
    "RequirementItem",
    "canonical_attrs",
    "get_extraction_schema",
    # Extractor
    "LLMExtractor",
    "create_extractor",
    # Data
    "load_cpu_specs",
    "load_gpu_specs",
]
