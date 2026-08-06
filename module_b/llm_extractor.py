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
    CPUInfo,
    GPUInfo,
    HardwareExtraction,
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

    def extract(
        self,
        spec_type: Literal["cpu", "gpu"],
        chunks: List[str],
        model: str = "gemma-2-27b-it",
        temperature: float = 0.0,
        max_retries: int = 2,
    ) -> HardwareExtraction:
        """Extract hardware specifications from text chunks.

        Args:
            spec_type: "cpu" or "gpu" - determines which schema to enforce.
            chunks: List of text fragments (paragraphs, table rows, etc.) containing specs.
            model: Model name to send to the backend (must be loaded in Ollama/LM Studio).
            temperature: Sampling temperature (0.0 = deterministic).
            max_retries: Number of retry attempts on failure.

        Returns:
            HardwareExtraction with cpu or gpu populated, is_sufficient_data flag set.

        Raises:
            httpx.HTTPStatusError: On HTTP error from backend.
            ValueError: If response parsing fails.
        """
        if not chunks:
            raise ValueError("At least one text chunk is required")

        # Build the extraction prompt
        prompt = self._build_prompt(spec_type, chunks)

        # Get JSON schema for constrained decoding
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "hardware_extraction",
                "schema": get_extraction_schema(spec_type),
                "strict": True,
            },
        }

        payload = self._backend.build_payload(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            temperature=temperature,
            response_format=response_format,
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

                # Parse JSON into HardwareExtraction model
                return HardwareExtraction.model_validate_json(content)

            except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as e:
                last_error = e
                if attempt < max_retries:
                    continue
                break

        raise RuntimeError("Extraction failed after retries") from last_error

    def _build_prompt(self, spec_type: Literal["cpu", "gpu"], chunks: List[str]) -> str:
        """Build the extraction prompt with strict instructions."""
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

        return f"""You are a precise hardware specification extractor.
Extract ONLY {type_upper} specifications from the provided text fragments.

CRITICAL RULES:
1. Return ONLY a JSON object matching the provided schema.
2. If a field is NOT explicitly stated in the text, set it to null.
3. DO NOT infer, guess, or hallucinate values.
4. If the text contains NO {type_upper} specifications, return cpu: null (or gpu: null) and is_sufficient_data: false.
5. Do NOT include any explanations, markdown, or extra text.

{field_guide}

SOURCE TEXT:
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