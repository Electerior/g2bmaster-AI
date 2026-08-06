"""module_b/hardware_schema.py

Pydantic master schema for CPU and GPU specifications.
The URL columns are deliberately omitted as per the user request.

Importers:
  - module_b.data_loader: imports CPUInfo, GPUInfo
  - module_b.llm_extractor: imports HardwareExtraction
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class CPUInfo(BaseModel):
    """CPU specification fields extracted from hardware data."""

    name: str = Field(..., description="Full CPU model name (e.g., 'Core i9-13900K', 'Ryzen 9 7950X')")
    vendor: Optional[str] = Field(None, description="CPU vendor (Intel, AMD, etc.)")
    generation: Optional[str] = Field(None, description="Microarchitecture generation (e.g., 'Raptor Lake', 'Zen 4')")
    socket: Optional[str] = Field(None, description="CPU socket (e.g., 'LGA1700', 'AM5')")
    lithography: Optional[str] = Field(None, description="Process node (e.g., '10nm', '7nm', '5nm')")
    cores: Optional[int] = Field(None, ge=1, description="Number of physical cores")
    threads: Optional[int] = Field(None, ge=1, description="Number of logical threads")
    base_clock_ghz: Optional[float] = Field(None, ge=0, description="Base clock frequency in GHz")
    boost_clock_ghz: Optional[float] = Field(None, ge=0, description="Max boost clock frequency in GHz")
    tdp_w: Optional[int] = Field(None, ge=0, description="Thermal Design Power in watts")
    memory_types: Optional[str] = Field(None, description="Supported memory types (e.g., 'DDR5, DDR4')")
    max_memory_gb: Optional[int] = Field(None, ge=0, description="Maximum supported memory in GB")
    memory_channels: Optional[int] = Field(None, ge=1, description="Number of memory channels")
    ecc_support: Optional[Literal["Yes", "No"]] = Field(None, description="ECC memory support")
    has_igpu: Optional[Literal["Yes", "No"]] = Field(None, description="Integrated graphics present")

    # URL omitted – not needed for extraction


class GPUInfo(BaseModel):
    """GPU specification fields extracted from hardware data."""

    manufacturer: Optional[str] = Field(None, description="GPU manufacturer (NVIDIA, AMD, Intel, etc.)")
    name: str = Field(..., description="Full GPU product name (e.g., 'GeForce RTX 4090')")
    gpu_name: Optional[str] = Field(None, alias="gpuName", description="GPU chip codename (e.g., 'AD102')")
    architecture: Optional[str] = Field(None, description="GPU architecture (e.g., 'Ada Lovelace', 'RDNA 3')")
    generation: Optional[str] = Field(None, description="Product generation")
    release_date: Optional[str] = Field(None, alias="releaseDate", description="Release date (YYYY-MM-DD)")
    base_clock: Optional[float] = Field(None, alias="baseClock", ge=0, description="Base clock in MHz")
    boost_clock: Optional[float] = Field(None, alias="boostClock", ge=0, description="Boost clock in MHz")
    memory_clock: Optional[float] = Field(None, alias="memoryClock", ge=0, description="Memory clock in MHz")
    memory_size: Optional[float] = Field(None, alias="memorySize", ge=0, description="VRAM size in GB")
    memory_type: Optional[str] = Field(None, alias="memoryType", description="Memory type (e.g., 'GDDR6X')")
    memory_bus: Optional[float] = Field(None, alias="memoryBus", ge=0, description="Memory bus width in bits")
    memory_bandwidth: Optional[float] = Field(None, alias="memoryBandwidth", ge=0, description="Memory bandwidth in GB/s")
    shaders: Optional[int] = Field(None, ge=0, alias="cudaCores", description="CUDA cores / Stream processors")
    tensor_cores: Optional[int] = Field(None, alias="tensorCores", ge=0, description="Tensor cores count")
    rt_cores: Optional[int] = Field(None, alias="rtCores", ge=0, description="Ray tracing cores count")
    fp32: Optional[float] = Field(None, ge=0, description="FP32 performance in GFLOPS")
    fp16: Optional[float] = Field(None, ge=0, description="FP16 performance in GFLOPS")
    fp64: Optional[float] = Field(None, ge=0, description="FP64 performance in GFLOPS")
    tdp: Optional[int] = Field(None, ge=0, description="Thermal Design Power in watts")
    cuda: Optional[float] = Field(None, ge=0, description="CUDA compute capability version")
    process_size: Optional[str] = Field(None, alias="processSize", description="Process node (e.g., '5 nm')")

    # URL omitted – not needed for extraction


class HardwareExtraction(BaseModel):
    """Master model that holds either CPU or GPU info (or both).

    The LLM is instructed to extract only the relevant type. If a field
    is not present in the source text, it MUST be set to null (not guessed).
    """

    cpu: Optional[CPUInfo] = Field(None, description="Extracted CPU specifications")
    gpu: Optional[GPUInfo] = Field(None, description="Extracted GPU specifications")
    is_sufficient_data: bool = Field(
        default=False,
        description="True if at least one hardware type had enough data to extract; False if source text was insufficient",
    )

    @model_validator(mode="after")
    def validate_at_least_one_present(self) -> "HardwareExtraction":
        """Ensure at least one of cpu or gpu is provided (not both None)."""
        if self.cpu is None and self.gpu is None:
            raise ValueError("At least one of `cpu` or `gpu` must be provided")
        return self

    @model_validator(mode="after")
    def set_sufficient_data_flag(self) -> "HardwareExtraction":
        """Auto-set is_sufficient_data based on whether any non-null fields exist."""
        cpu_has_data = self.cpu is not None and any(
            v is not None for v in self.cpu.model_dump().values() if v != self.cpu.name
        )
        gpu_has_data = self.gpu is not None and any(
            v is not None for v in self.gpu.model_dump().values() if v != self.gpu.name
        )
        self.is_sufficient_data = cpu_has_data or gpu_has_data
        return self

    def to_json_schema(self) -> dict[str, Any]:
        """Generate JSON schema for constrained decoding (response_format)."""
        return self.model_json_schema()


def get_extraction_schema(hardware_type: Literal["cpu", "gpu", "both"]) -> dict[str, Any]:
    """Get JSON schema constrained to specific hardware type for LLM extraction.

    Args:
        hardware_type: "cpu" for CPU-only, "gpu" for GPU-only, "both" for full schema.

    Returns:
        JSON schema dict compatible with OpenAI response_format.
    """
    if hardware_type == "cpu":
        return {
            "type": "object",
            "properties": {
                "cpu": CPUInfo.model_json_schema(),
                "gpu": {"type": "null"},
                "is_sufficient_data": {"type": "boolean"},
            },
            "required": ["cpu", "gpu", "is_sufficient_data"],
        }
    elif hardware_type == "gpu":
        return {
            "type": "object",
            "properties": {
                "cpu": {"type": "null"},
                "gpu": GPUInfo.model_json_schema(),
                "is_sufficient_data": {"type": "boolean"},
            },
            "required": ["cpu", "gpu", "is_sufficient_data"],
        }
    else:
        return HardwareExtraction.model_json_schema()