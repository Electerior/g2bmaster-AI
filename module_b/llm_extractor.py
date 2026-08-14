"""Module B: LLM Extractor with Engine-Agnostic Backend Support.

Abstracts local LLM communication (Ollama, LM Studio) behind a common interface.
Uses OpenAI-compatible chat/completions API with JSON Schema constrained decoding.

Importers: None yet (new module for feature development).
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any, List, Literal, Optional
from urllib.parse import urlparse

import httpx

from .hardware_schema import (
    ATTR_UNITS,
    CATEGORY_ATTRS,
    ITEM_CATEGORIES,
    CPUInfo,
    Evidence,
    GPUInfo,
    HardwareExtraction,
    RequirementExtraction,
    get_extraction_schema,
)


# Allowlist for custom backend URLs (prevents SSRF)
_ALLOWED_HOST_PATTERNS = (
    r"^localhost$",
    r"^127\.0\.0\.1$",
    r"^host\.docker\.internal$",
    r"^\[::1\]$",
)


def _validate_custom_backend_url(base_url: str) -> str:
    """Validate a custom backend URL against allowlist to prevent SSRF.

    Args:
        base_url: The base URL to validate (e.g., "http://localhost:1234/v1")

    Returns:
        The validated base_url

    Raises:
        ValueError: If URL is invalid or host not in allowlist
    """
    try:
        parsed = urlparse(base_url)
    except Exception as e:
        raise ValueError(f"Invalid URL: {e}")

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https schemes allowed, got: {parsed.scheme}")

    # Extract hostname (without port)
    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError("Missing hostname in URL")

    # Check against allowlist
    allowed = any(re.match(pattern, hostname) for pattern in _ALLOWED_HOST_PATTERNS)
    if not allowed:
        raise ValueError(
            f"Host '{hostname}' not allowed. Custom backends must use localhost, "
            f"127.0.0.1, or host.docker.internal"
        )

    # Validate port if present
    if parsed.port is not None and not (1 <= parsed.port <= 65535):
        raise ValueError(f"Invalid port: {parsed.port}")

    return base_url


def _condense(text: str) -> tuple[str, List[int]]:
    """공백을 걷어낸 문자열과, 각 글자가 원문 몇 번째였는지의 표를 함께 만든다."""
    chars: List[str] = []
    positions: List[int] = []
    for index, char in enumerate(text):
        if not char.isspace():
            chars.append(char)
            positions.append(index)
    return "".join(chars), positions


def _locate(quote: str, source: str) -> Optional[Evidence]:
    """근거 문장을 원문에서 찾아 좌표를 붙인다.

    먼저 그대로 찾고, 없으면 **공백만 무시하고** 다시 찾는다. 모델이 표를 읽으면서
    줄바꿈·자간을 다르게 옮기는 일은 흔한데, 그건 지어낸 것이 아니라 같은 문장이다.

    공백을 무시해도 좌표는 **원문 기준**으로 돌려준다 — 정규화본의 좌표를 주면
    백엔드가 원문과 대조할 때 전부 어긋난다(`CLAUDE.md §2-6`).
    찾지 못하면 `found=False` 로 남긴다. 지어낸 문장이라는 사실이 곧 판정 재료다.
    """
    quote = (quote or "").strip()
    if not quote:
        return None

    exact = source.find(quote)
    if exact >= 0:
        return Evidence(quote=quote, offset=exact, length=len(quote), found=True)

    haystack, positions = _condense(source)
    needle, _ = _condense(quote)
    if needle:
        hit = haystack.find(needle)
        if hit >= 0:
            start = positions[hit]
            end = positions[hit + len(needle) - 1] + 1
            return Evidence(quote=quote, offset=start, length=end - start, found=True)

    return Evidence(quote=quote, offset=-1, length=0, found=False)


def _surviving_quotes(quotes: dict, source: str) -> dict:
    """원문에서 확인되는 인용만 남긴다.

    값은 건드리지 않는다. 인용이 사라진 필드는 "값은 있는데 출처가 없다"는 뜻이고,
    사전을 쌓는 쪽이 그 필드를 의심할 수 있어야 한다 — 값까지 지우면 무엇이
    의심스러웠는지도 함께 사라진다.
    """
    return {
        field: quote
        for field, quote in (quotes or {}).items()
        if (located := _locate(quote, source)) is not None and located.found
    }


class LLMExtractorBackend(ABC):
    """Abstract base for local LLM backends (Ollama, LM Studio, etc.)."""

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Base URL for the OpenAI-compatible chat/completions endpoint."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name."""
        ...

    def build_payload(
        self,
        messages: List[dict[str, str]],
        model: str,
        temperature: float,
        response_format: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Build the request payload for the chat completions endpoint."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        return payload


class OllamaBackend(LLMExtractorBackend):
    """Ollama local LLM backend (default: http://localhost:11434/v1)."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def name(self) -> str:
        return "Ollama"


class LMStudioBackend(LLMExtractorBackend):
    """LM Studio local LLM backend (default: http://localhost:1234/v1)."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = base_url or os.getenv("LMS_BASE_URL", "http://localhost:1234/v1")

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def name(self) -> str:
        return "LM Studio"


class LLMExtractor:
    """Main extractor class that uses a pluggable backend.

    Usage:
        # Auto-detect or specify backend
        extractor = LLMExtractor(backend="lms")  # or "ollama"
        # Or custom URL
        extractor = LLMExtractor(backend="custom", base_url="http://my-server:1234/v1")

        # Extract hardware specs
        result = extractor.extract(
            spec_type="cpu",
            chunks=["Intel Core i9-13900K specs..."],
            model="gemma-2-27b-it",
        )
    """

    _BACKEND_REGISTRY = {
        "ollama": OllamaBackend,
        "lms": LMStudioBackend,
        "lmstudio": LMStudioBackend,
    }

    def __init__(
        self,
        backend: Literal["ollama", "lms", "lmstudio", "custom"] = "ollama",
        base_url: Optional[str] = None,
        timeout: float = 60.0,
    ) -> None:
        """Initialize the extractor with a backend.

        Args:
            backend: Backend type ("ollama", "lms", "lmstudio", or "custom").
            base_url: Required if backend="custom". Overrides defaults for others.
            timeout: HTTP timeout in seconds.
        """
        if backend == "custom":
            if not base_url:
                raise ValueError("base_url is required when backend='custom'")

            # Validate URL against allowlist to prevent SSRF
            base_url = _validate_custom_backend_url(base_url)

            class CustomBackend(LLMExtractorBackend):
                def __init__(self, url: str):
                    self._url = url

                @property
                def base_url(self) -> str:
                    return self._url

                @property
                def name(self) -> str:
                    return f"Custom ({self._url})"

            self._backend = CustomBackend(base_url)
        elif backend in self._BACKEND_REGISTRY:
            self._backend = self._BACKEND_REGISTRY[backend](base_url)
        else:
            raise ValueError(
                f"Unknown backend: {backend}. "
                f"Available: {list(self._BACKEND_REGISTRY.keys())} + 'custom'"
            )

        self._client = httpx.Client(timeout=timeout)
        self._chat_endpoint = f"{self._backend.base_url}/chat/completions"

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def backend_url(self) -> str:
        return self._backend.base_url

    def _request_json(
        self,
        prompt: str,
        schema_name: str,
        schema: dict,
        model: str,
        temperature: float,
        max_retries: int,
    ) -> str:
        """제약 디코딩으로 JSON 하나를 받아 온다. 두 방향이 공유하는 유일한 경로다."""
        payload = self._backend.build_payload(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=temperature,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            },
        )

        last_error: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                response = self._client.post(self._chat_endpoint, json=payload)
                response.raise_for_status()
                data = response.json()

                content = data["choices"][0]["message"]["content"]
                if not content:
                    raise ValueError("Empty response content from LLM")
                return content

            except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as e:
                last_error = e
                if attempt < max_retries:
                    continue
                break

        raise RuntimeError("Extraction failed after retries") from last_error

    def extract(
        self,
        spec_type: Literal["cpu", "gpu"],
        chunks: List[str],
        model: str = "gemma-2-27b-it",
        temperature: float = 0.0,
        max_retries: int = 2,
        target: Optional[str] = None,
    ) -> HardwareExtraction:
        """데이터시트 방향 — 제조사 스펙 문서에서 제품 하나의 값을 뽑는다.

        Args:
            spec_type: "cpu" or "gpu" - determines which schema to enforce.
            chunks: List of text fragments (paragraphs, table rows, etc.) containing specs.
            model: Model name to send to the backend (must be loaded in Ollama/LM Studio).
            temperature: Sampling temperature (0.0 = deterministic).
            max_retries: Number of retry attempts on failure.
            target: 문서에 여러 제품이 있을 때 뽑을 제품명.

        Returns:
            HardwareExtraction with cpu or gpu populated, is_sufficient_data flag set.

        Raises:
            httpx.HTTPStatusError: On HTTP error from backend.
            ValueError: If response parsing fails.
        """
        if not chunks:
            raise ValueError("At least one text chunk is required")

        content = self._request_json(
            self._build_prompt(spec_type, chunks, target),
            "hardware_extraction",
            get_extraction_schema(spec_type),
            model,
            temperature,
            max_retries,
        )
        result = HardwareExtraction.model_validate_json(content)
        source = "\n---\n".join(chunks)
        for info in (result.cpu, result.gpu):
            if info is not None:
                info.quotes = _surviving_quotes(info.quotes, source)
        return result

    def extract_requirements(
        self,
        chunks: List[str],
        model: str = "gemma-2-27b-it",
        temperature: float = 0.0,
        max_retries: int = 2,
    ) -> RequirementExtraction:
        """규격서 방향 — 조달 규격서가 요구한 품목과 조건을 뽑는다.

        반환 직전에 근거 문장의 원문 좌표를 우리가 찾아 붙인다(`CLAUDE.md §2-5`).
        **찾지 못한 근거를 버리지는 않는다** — 지어낸 문장이라는 사실 자체가
        백엔드가 판정할 재료이고, 여기서 지우면 그 판정이 불가능해진다.
        """
        if not chunks:
            raise ValueError("At least one text chunk is required")

        content = self._request_json(
            self._build_requirement_prompt(chunks),
            "requirement_extraction",
            get_extraction_schema("requirement"),
            model,
            temperature,
            max_retries,
        )
        result = RequirementExtraction.model_validate_json(content)

        source = "\n---\n".join(chunks)
        for item in result.items:
            item.evidence_span = _locate(item.evidence, source)
            for child in item.children:
                child.evidence_span = _locate(child.evidence, source)
        return result

    def _build_prompt(
        self,
        spec_type: Literal["cpu", "gpu"],
        chunks: List[str],
        target: Optional[str] = None,
    ) -> str:
        """데이터시트 방향 프롬프트 — 제조사 스펙 문서에서 *제품의 값*을 옮겨 적는다."""
        type_upper = spec_type.upper()

        if spec_type == "cpu":
            field_guide = """
CPU Fields to extract (set to null if not in text):
- name (required): Full model name, e.g., "Core i9-13900K"
- vendor: "Intel" or "AMD"
- generation: Microarchitecture, e.g., "Raptor Lake"
- socket: e.g., "LGA1700"
- lithography: Process node, e.g., "10nm" or "Intel 7"
- cores: Integer, physical cores
- threads: Integer, logical threads
- base_clock_ghz: Float, base frequency in GHz
- boost_clock_ghz: Float, max turbo in GHz
- tdp_w: Integer, TDP in watts
- memory_types: e.g., "DDR5, DDR4"
- max_memory_gb: Integer
- memory_channels: Integer
- ecc_support: "Yes" or "No"
- has_igpu: "Yes" or "No"
"""
        else:
            field_guide = """
GPU Fields to extract (set to null if not in text):
- manufacturer: "NVIDIA", "AMD", "Intel", etc.
- name (required): Full product name, e.g., "GeForce RTX 4090"
- gpu_name: Chip codename, e.g., "AD102"
- architecture: e.g., "Ada Lovelace", "RDNA 3"
- generation: Product generation
- release_date: "YYYY-MM-DD"
- base_clock: MHz (Float)
- boost_clock: MHz (Float)
- memory_clock: MHz (Float)
- memory_size: VRAM in GB (Float)
- memory_type: e.g., "GDDR6X"
- memory_bus: Bus width in bits (Float)
- memory_bandwidth: GB/s (Float)
- shaders: CUDA cores / Stream processors (Integer)
- tensor_cores: Integer
- rt_cores: Integer
- fp32: GFLOPS (Integer)
- fp16: GFLOPS (Integer)
- fp64: GFLOPS (Integer)
- tdp: Watts (Integer)
- cuda: Compute capability (Integer)
- process_size: e.g., "5 nm"
"""

        chunks_text = "\n---\n".join(chunks)
        target_rule = (
            f'6. The text may describe several products. Extract "{target}" and ignore the rest.'
            if target
            else "6. If the text describes several products, extract the first fully specified\n"
            "   one and ignore the rest. Never merge fields from two products into one."
        )

        return f"""You extract hardware specifications from manufacturer datasheets and spec pages.
Extract ONLY {type_upper} specifications from the provided text fragments.

RULES
1. Copy values only. If a field is not stated in the text, set it to null.
   Never infer, never fill from prior knowledge, never turn a marketing claim
   ("blazing fast memory") into a number.
2. Normalize each value to the unit declared for its field (GB, GHz, MHz, W, GB/s).
   Convert only when the source states the same quantity in another unit
   (e.g. "24576 MB" -> 24). A unit conversion is arithmetic; a guess is not.
   Never convert across different quantities (clock is not bandwidth).
3. `name` is the full retail product name exactly as printed, e.g.
   "GeForce RTX 4090", "RTX PRO 6000 Blackwell Server Edition".
   Do not shorten, expand, or tidy it.
4. For every non-null field, put the exact source substring you read it from into
   `quotes`, keyed by the field name. Copy it verbatim — do not paraphrase, do not
   re-wrap. A value whose quote cannot be found in the source will be discarded.
5. If the text contains NO {type_upper} specifications, return cpu: null (or gpu: null)
   and is_sufficient_data: false. Do not pad the object to look complete.
{target_rule}
7. Return ONLY the JSON object. No explanations, no markdown fences.

{field_guide}

SOURCE TEXT:
{chunks_text}
"""

    def _build_requirement_prompt(self, chunks: List[str]) -> str:
        """규격서 방향 프롬프트 — 조달 규격서가 *요구한 것*을 조건으로 분해한다.

        문서가 한국어이므로 프롬프트도 한국어로 쓴다. 영어로 지시하면 모델이
        `이상`·`동급` 같은 조달 어휘를 옮기는 과정에서 뜻을 흘린다.
        """
        chunks_text = "\n---\n".join(chunks)
        attr_guide = "\n".join(
            f"      {category}: {', '.join(attrs)}"
            for category, attrs in CATEGORY_ATTRS.items()
            if attrs
        )

        return f"""너는 조달 규격서에서 **구매 요구사항**을 뽑는다.
제품을 고르는 것이 아니라, 문서가 요구한 것을 그대로 옮겨 적는 일이다.

품목마다 하나씩 채운다.

- category: {' · '.join(ITEM_CATEGORIES)} 중 하나
- name: 규격서에 **제품명·모델명이 적혀 있으면** 그대로. 없으면 null.
- named: 위 name 이 문서에 적혀 있었으면 true, 없어서 null 로 두었으면 false.
- qty / unit: 수량과 단위(대·장·식·SET). 수량이 없으면 qty=1.
- constraints: 요구된 사양을 조건 하나씩으로 분해한다.
    {{"attr": …, "op": "gte|lte|eq|approx", "value": …, "unit": …, "raw": "원문 조각"}}
    op 는 이상=gte · 이하=lte · 정확히=eq · 내외/동급=approx 다.
    "24TB 이상"          -> {{"attr":"capacity_gb","op":"gte","value":24000,"unit":"GB","raw":"24TB 이상"}}
    "CUDA 24064개 이상"  -> {{"attr":"shaders","op":"gte","value":24064,"unit":null,"raw":"CUDA 코어 24064개 이상"}}
    "DDR5 ECC"           -> {{"attr":"memory_type","op":"eq","value":"DDR5","unit":null,"raw":"DDR5 ECC"}}
    attr 은 **그 품목의 category 에 해당하는 것만** 고른다:
{attr_guide}
    카테고리를 넘겨 쓰지 마라. RAM 용량은 capacity_gb 이지 memory_size_gb 가 아니다 —
    memory_size_gb 는 GPU 의 VRAM 자리다. 잘못 넣으면 대조할 때 64GB 메모리가
    VRAM 요구로 읽힌다.
    ecc 는 true/false 로 적는다. 목록에 없는 성질은 notes 에 문장으로 적는다.
- allow_equivalent: "동급"·"동등 이상"·"또는 동급 제품" 이 붙어 있으면 true.
- prebuilt: 이 품목이 조립 부품이 아니라 **완제품·베어본·서버 섀시 단위**로
  요구되었으면 true.
- children: SET 으로 묶여 있으면 그 구성품을 여기에 넣는다.
- notes: 수치가 아닌 요구(국내 A/S, 인증, 납품 조건 등).
- evidence: 이 품목의 근거가 된 **규격서 원문 한 줄을 그대로 복사**한다.

지켜야 할 것

1. **제품을 추측하지 마라.** 사양만 적혀 있으면 name 은 null 이고 named 는 false 다.
   여기서 모델명을 지어내면 뒤 단계의 탐색이 그 거짓 이름을 사실로 굳힌다.
   "이 정도 사양이면 보통 RTX 4090" 같은 판단은 네 일이 아니다.
2. 단위는 GB·GHz·W·MHz·GB/s 로 정규화한다. TB→GB 처럼 문서가 같은 값을 다른
   단위로 쓴 경우만 환산한다. 환산은 계산이고 짐작은 추론이다 — 짐작은 하지 마라.
3. evidence 는 **원문 한 줄을 그대로** 복사한다. 요약하거나 다듬지 마라.
   근거가 여러 줄에 흩어져 있으면 **가장 핵심이 되는 한 줄만** 고른다.
   여러 줄을 " / " 같은 것으로 이어 붙이지 마라 — 원문에 없는 문장이 되어
   그 품목은 근거 없는 것으로 처리된다.
4. 소프트웨어·설치·교육·보증·운송은 품목이 아니다. 다만 완제품 구성에 포함된
   것으로 적혀 있으면 그 완제품의 notes 에 남긴다.
5. 표가 SET 단위로 적혀 있으면 SET 을 완제품 1건(prebuilt=true)으로 잡고 부품을
   children 에 넣는다. 부품을 SET 밖으로 꺼내 평평하게 만들지 마라 — 수량이 어긋난다.
6. 규격서에 하드웨어 요구가 전혀 없으면 items 를 빈 배열로 둔다. 억지로 채우지 마라.
7. JSON 객체 하나로만 답한다. 설명도 코드펜스도 붙이지 마라.

보기 — 같은 문서의 두 줄이 이렇게 갈린다.

  "Processor: Intel® Xeon® 6530P Processor 144M Cache, 2.30 GHz, 32core x2"
   → 제품명이 적혀 있다.
     {{"category":"CPU","name":"Intel Xeon 6530P","named":true,"qty":2,
       "constraints":[{{"attr":"cores","op":"eq","value":32,"unit":null,"raw":"32core"}}],
       "evidence":"Processor: Intel® Xeon® 6530P Processor 144M Cache, 2.30 GHz, 32core x2"}}

  "Memory: 64GB DDR5 R/E x32"
   → 제품명이 없다. 지어내지 말고 null 로 둔다.
     {{"category":"RAM","name":null,"named":false,"qty":32,
       "constraints":[{{"attr":"capacity_gb","op":"eq","value":64,"unit":"GB","raw":"64GB"}},
                      {{"attr":"memory_type","op":"eq","value":"DDR5","unit":null,"raw":"DDR5"}}],
       "evidence":"Memory: 64GB DDR5 R/E x32"}}

규격서 원문:
{chunks_text}
"""

    def extract_batch(
        self,
        spec_type: Literal["cpu", "gpu"],
        chunks_list: List[List[str]],
        model: str = "gemma-2-27b-it",
        temperature: float = 0.0,
    ) -> List[HardwareExtraction]:
        """Extract from multiple independent chunk sets (e.g., multiple products)."""
        return [
            self.extract(spec_type, chunks, model, temperature)
            for chunks in chunks_list
        ]

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "LLMExtractor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def create_extractor(
    backend: Literal["ollama", "lms", "lmstudio", "custom"] = "ollama",
    base_url: Optional[str] = None,
    timeout: float = 60.0,
) -> LLMExtractor:
    """Factory function to create an LLMExtractor instance."""
    return LLMExtractor(backend=backend, base_url=base_url, timeout=timeout)