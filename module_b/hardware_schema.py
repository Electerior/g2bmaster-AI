"""module_b/hardware_schema.py

두 방향의 스키마를 한 어휘로 묶는다.

  - **데이터시트 방향** (`CPUInfo`/`GPUInfo`) — 제조사 스펙 문서에서 뽑은 *제품의 값*.
    스펙 사전(CSV·DB)의 모양이기도 하다.
  - **규격서 방향** (`RequirementItem`) — 조달 규격서가 *요구한 것*. 값이 아니라
    조건이므로 `{attr, op, value}` 로 분해한다.

두 방향이 같은 속성 이름을 쓰는 것이 이 파일의 존재 이유다. `"96GB 이상"` 요구가
`{attr: "memory_size_gb", op: "gte", value: 96}` 이고 제품이 `memory_size_gb=96` 이면,
둘의 대조는 **추론이 아니라 산수**가 된다. 문자열로 제품명을 맞춰 보는 짓 —
`"Alphacool ES GPU 워터 블록 Nvidia H200 141GB"` 가 GPU 로 통과하던 — 을 그만두려면
양쪽이 구조화돼 있어야 한다.

다만 `CPUInfo`/`GPUInfo` 의 **필드명은 바꾸지 않는다.** `data_loader` 가
`CPUInfo(**row)` 로 CSV 헤더를 그대로 밀어 넣기 때문에, 개명하면 `/api/specs/cpu|gpu`
가 조용히 빈 값을 내기 시작한다. 공용 어휘는 개명 대신 `canonical_attrs()` 매핑으로 만든다.

Importers:
  - module_b.data_loader: imports CPUInfo, GPUInfo
  - module_b.llm_extractor: imports HardwareExtraction, RequirementExtraction
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── 공용 속성 어휘 ────────────────────────────────────────────────────────────
#
# 규격서가 요구할 수 있고 데이터시트가 답할 수 있는 속성만 담는다. 단위를 여기서
# 못박아야 "24TB 이상" 과 "memory_size_gb=24000" 이 같은 자로 재진다.
#
# 값이 None 인 것은 단위 없는 속성(개수·문자열)이다.
ATTR_UNITS: Dict[str, Optional[str]] = {
    # 공통
    "tdp_w": "W",
    "form_factor": None,
    "interface": None,
    # CPU
    "cores": None,
    "threads": None,
    "base_clock_ghz": "GHz",
    "boost_clock_ghz": "GHz",
    "socket": None,
    "memory_channels": None,
    "max_memory_gb": "GB",
    "ecc": None,
    # GPU
    "memory_size_gb": "GB",
    "memory_type": None,
    "memory_bus_bits": "bit",
    "memory_bandwidth_gbs": "GB/s",
    "shaders": None,
    "tensor_cores": None,
    "rt_cores": None,
    "fp32_gflops": "GFLOPS",
    "fp16_gflops": "GFLOPS",
    "fp64_gflops": "GFLOPS",
    # 메모리·저장장치
    "capacity_gb": "GB",
    "speed_mts": "MT/s",
    "rpm": "RPM",
    # 파워
    "watts": "W",
    "efficiency": None,
    # 네트워크
    "speed_gbps": "Gbps",
    "ports": None,
    # 모니터
    "size_inch": "inch",
    "resolution": None,
}

# 데이터시트 필드 → 공용 어휘. 개명 대신 이 표가 다리를 놓는다.
_CPU_TO_ATTR: Dict[str, str] = {
    "cores": "cores",
    "threads": "threads",
    "base_clock_ghz": "base_clock_ghz",
    "boost_clock_ghz": "boost_clock_ghz",
    "tdp_w": "tdp_w",
    "socket": "socket",
    "memory_channels": "memory_channels",
    "max_memory_gb": "max_memory_gb",
    "memory_types": "memory_type",
    "ecc_support": "ecc",
}

_GPU_TO_ATTR: Dict[str, str] = {
    "memory_size": "memory_size_gb",
    "memory_type": "memory_type",
    "memory_bus": "memory_bus_bits",
    "memory_bandwidth": "memory_bandwidth_gbs",
    "shaders": "shaders",
    "tensor_cores": "tensor_cores",
    "rt_cores": "rt_cores",
    "fp32": "fp32_gflops",
    "fp16": "fp16_gflops",
    "fp64": "fp64_gflops",
    "tdp": "tdp_w",
}


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

    # 값마다 "어느 문장에서 읽었는가". 사전에 쌓인 값도 나중에 의심할 수 있어야 한다 —
    # 출처 대조는 문서를 이해하지 않으므로 어떤 문서에서도 성립하는 유일한 검증이다.
    quotes: Dict[str, str] = Field(
        default_factory=dict,
        description="field name -> verbatim source substring the value was read from",
    )

    # URL omitted – not needed for extraction


class GPUInfo(BaseModel):
    """GPU specification fields extracted from hardware data.

    별칭과 필드명을 **둘 다** 받는다. 별칭만 받던 동안 `shaders` 열이 통째로 버려지고
    있었다 — CSV 헤더는 `shaders` 인데 별칭이 `cudaCores` 라 2824행 전부 None 이었다.
    예외도 경고도 없이 값만 사라지는 자리라(pydantic 은 모르는 키를 무시한다) 여기서
    막는다. CUDA 코어 수는 "CUDA 24064개 이상" 같은 요구를 대조할 때 핵심 속성이다.
    """

    model_config = ConfigDict(populate_by_name=True)

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

    quotes: Dict[str, str] = Field(
        default_factory=dict,
        description="field name -> verbatim source substring the value was read from",
    )

    # URL omitted – not needed for extraction


def canonical_attrs(info: Union[CPUInfo, GPUInfo]) -> Dict[str, Any]:
    """제품 스펙을 공용 어휘로 옮긴다 — 규격서 요구와 같은 자를 쓰게 하는 다리.

    값이 없는(None) 속성은 넣지 않는다. "모른다"와 "0"은 다르고, 없는 값을 0 으로
    채우면 `{op:"gte", value:96}` 이 조용히 거짓이 된다.
    """
    table = _CPU_TO_ATTR if isinstance(info, CPUInfo) else _GPU_TO_ATTR
    out: Dict[str, Any] = {}
    for field, attr in table.items():
        value = getattr(info, field, None)
        if value is None:
            continue
        # CPU 의 ECC 는 "Yes"/"No" 로 저장돼 있다. 규격서는 "ECC 지원" 을 요구하므로
        # 참·거짓으로 옮겨 둔다 — 문자열끼리 비교하게 두면 대조 쪽이 다시 추측한다.
        if attr == "ecc":
            out[attr] = value == "Yes"
        else:
            out[attr] = value
    return out


class HardwareExtraction(BaseModel):
    """데이터시트 방향의 결과. CPU 또는 GPU 한 제품."""

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
        """이름 말고 실제 스펙이 하나라도 잡혔는가.

        `quotes` 는 스펙이 아니라 출처이므로 세지 않는다 — 세면 인용만 달린 빈
        추출이 "충분한 데이터"로 통과한다.
        """
        def _has_spec(info: Union[CPUInfo, GPUInfo, None]) -> bool:
            if info is None:
                return False
            return any(
                value is not None
                for key, value in info.model_dump().items()
                if key not in ("name", "quotes")
            )

        self.is_sufficient_data = _has_spec(self.cpu) or _has_spec(self.gpu)
        return self

    def to_json_schema(self) -> dict[str, Any]:
        """Generate JSON schema for constrained decoding (response_format)."""
        return self.model_json_schema()


# ── 규격서 방향 ───────────────────────────────────────────────────────────────

#: 카테고리마다 쓸 수 있는 속성. 어휘 전체를 통으로 주면 모델이 자리를 헷갈린다 —
#: 실측에서 RAM 용량이 `memory_size_gb`(GPU VRAM 자리)로 들어왔다. 그러면 대조할 때
#: 64GB 메모리가 VRAM 요구로 읽혀 조용히 틀린다.
CATEGORY_ATTRS: Dict[str, tuple] = {
    "CPU": ("cores", "threads", "base_clock_ghz", "boost_clock_ghz", "tdp_w",
            "socket", "memory_channels", "max_memory_gb", "memory_type", "ecc"),
    "GPU": ("memory_size_gb", "memory_type", "memory_bus_bits", "memory_bandwidth_gbs",
            "shaders", "tensor_cores", "rt_cores", "fp32_gflops", "fp16_gflops",
            "fp64_gflops", "tdp_w", "interface"),
    "RAM": ("capacity_gb", "memory_type", "speed_mts", "ecc", "form_factor"),
    "SSD": ("capacity_gb", "interface", "form_factor"),
    "HDD": ("capacity_gb", "interface", "form_factor", "rpm"),
    "메인보드": ("socket", "memory_channels", "form_factor", "ports"),
    "파워": ("watts", "efficiency"),
    "케이스": ("form_factor",),
    "쿨러": ("tdp_w", "form_factor"),
    "네트워크": ("speed_gbps", "ports", "interface"),
    "모니터": ("size_inch", "resolution"),
    "완제품": ("form_factor", "watts", "tdp_w"),
    "기타": (),
}

ITEM_CATEGORIES = (
    "CPU", "GPU", "RAM", "SSD", "HDD", "메인보드", "파워", "케이스",
    "쿨러", "네트워크", "모니터", "완제품", "기타",
)

CategoryName = Literal[
    "CPU", "GPU", "RAM", "SSD", "HDD", "메인보드", "파워", "케이스",
    "쿨러", "네트워크", "모니터", "완제품", "기타",
]

#: 비교 연산자. 조달 규격서의 "이상/이하/정확히/내외" 에 대응한다.
#: `approx` 는 "동급"·"내외"처럼 폭이 정해지지 않은 요구다 — **판정하지 말고
#: 사람에게 넘기라는 표시**로 쓴다. 여기에 임의의 허용 오차를 넣는 순간
#: 백엔드가 다시 적합성을 추론하게 된다.
ConstraintOp = Literal["gte", "lte", "eq", "approx"]

#: 참·거짓으로만 뜻이 있는 속성. 규격서 표기는 제각각이라 여기서 접는다.
_FLAG_ATTRS = frozenset({"ecc"})
_FLAG_FALSE = frozenset({"no", "false", "미지원", "없음", "비지원", "non-ecc", "nonecc"})


class Evidence(BaseModel):
    """근거 문장과 그 원문 좌표.

    `CLAUDE.md §2-5` — 인용은 좌표까지 실어 보내고 **반환 직전 우리가 자체 검증한다.**
    최종 채택은 백엔드가 하지만, 좌표가 있으면 백엔드의 대조가 탐색이 아니라 조회가 된다.

    `found=False` 는 "LLM 이 원문에 없는 문장을 지어냈다"는 뜻이다. 여기서 버리지
    않고 그대로 실어 보낸다 — 버리면 백엔드가 판정할 재료 자체가 사라진다.
    """

    quote: str
    offset: int = -1
    length: int = 0
    found: bool = False


class Constraint(BaseModel):
    """규격서가 요구한 조건 하나."""

    attr: str = Field(..., description="ATTR_UNITS 의 속성 이름")
    op: ConstraintOp = Field(..., description="gte(이상) · lte(이하) · eq(정확히) · approx(내외·동급)")
    value: Union[bool, float, str] = Field(..., description="정규화된 값. 숫자 속성이면 숫자")
    unit: Optional[str] = Field(None, description="정규화 단위 (GB·GHz·W …)")
    raw: str = Field(..., description="이 조건이 나온 원문 조각 그대로")

    @model_validator(mode="after")
    def _normalize_flag(self) -> "Constraint":
        """참·거짓 속성은 표기가 아니라 값으로 만든다.

        규격서는 ECC 를 `R/E`·`ECC`·`지원`처럼 제각각으로 쓴다(실측: `ecc="R/E"`).
        표기를 그대로 두면 대조하는 쪽이 "R/E 가 ECC 인가"를 판단하게 되고, 그건
        추론이다. 어휘를 아는 여기서 접는다.
        """
        if self.attr in _FLAG_ATTRS and isinstance(self.value, str):
            token = self.value.strip().lower()
            if token in _FLAG_FALSE:
                self.value = False
            elif token:
                # "R/E"·"ECC"·"지원"·"Yes" — 값이 적혔다는 것 자체가 요구했다는 뜻이다.
                self.value = True
        return self


class RequirementPart(BaseModel):
    """세트 구성품. 중첩을 한 겹으로 끊어 constrained decoding 이 재귀를 만나지 않게 한다."""

    category: CategoryName
    name: Optional[str] = None
    named: bool = False
    qty: int = Field(default=1, ge=1)
    unit: Optional[str] = None
    constraints: List[Constraint] = Field(default_factory=list)
    allow_equivalent: bool = False
    evidence: str = ""
    #: 우리가 채운다. LLM 은 좌표를 못 센다 — 세게 하면 그럴듯한 숫자를 지어낸다.
    evidence_span: Optional[Evidence] = None

    @model_validator(mode="after")
    def _reconcile_name(self) -> "RequirementPart":
        return _reconcile(self)


class RequirementItem(BaseModel):
    """규격서가 요구한 품목 하나."""

    category: CategoryName
    name: Optional[str] = Field(None, description="규격서에 적힌 제품명. 없으면 null")
    named: bool = Field(False, description="제품명이 문서에 적혀 있었는가")
    qty: int = Field(default=1, ge=1)
    unit: Optional[str] = Field(None, description="대·장·식·SET 등")
    constraints: List[Constraint] = Field(default_factory=list)
    allow_equivalent: bool = Field(False, description='"동급"·"동등 이상" 이 붙어 있었는가')
    prebuilt: bool = Field(False, description="완제품·베어본·섀시 단위로 요구되었는가")
    children: List[RequirementPart] = Field(default_factory=list, description="SET 구성품")
    notes: List[str] = Field(default_factory=list, description="수치가 아닌 요구(A/S·인증 등)")
    evidence: str = Field("", description="근거가 된 규격서 원문 한 줄 그대로")
    evidence_span: Optional[Evidence] = Field(None, description="우리가 채우는 원문 좌표")

    @model_validator(mode="after")
    def _reconcile_name(self) -> "RequirementItem":
        return _reconcile(self)


def _reconcile(item: Union[RequirementItem, RequirementPart]):
    """`named` 와 `name` 의 아귀를 맞춘다.

    LLM 출력이라 어긋날 수 있는데, **여기서 예외를 던지면 배치 하나가 통째로 죽는다.**
    그래서 거절하지 않고 안전한 쪽으로 접는다 — 이름이 없으면 `named=False` 다.
    반대로 이름이 있는데 `named=False` 인 것은 손대지 않는다. "문서에 없던 이름을
    모델이 붙였다"는 뜻이고, 그건 탐색 단계가 알아야 할 사실이다.
    """
    if not (item.name or "").strip():
        item.name = None
        item.named = False
    return item


class RequirementExtraction(BaseModel):
    """규격서 방향의 결과."""

    items: List[RequirementItem] = Field(default_factory=list)


#: LLM 에게 요구하지 않는 필드. 우리가 채우므로 스키마에서 빼야 한다 —
#: 남겨 두면 constrained decoding 이 좌표를 지어내게 만든다.
_SERVER_FILLED_FIELDS = ("evidence_span",)


def _llm_facing_requirement_schema() -> dict[str, Any]:
    """규격서 방향 스키마에서 서버가 채우는 필드를 걷어낸다."""
    schema = RequirementExtraction.model_json_schema()
    for definition in schema.get("$defs", {}).values():
        properties = definition.get("properties")
        if not properties:
            continue
        for field in _SERVER_FILLED_FIELDS:
            properties.pop(field, None)
        if "required" in definition:
            definition["required"] = [
                name for name in definition["required"] if name not in _SERVER_FILLED_FIELDS
            ]
    return schema


def get_extraction_schema(hardware_type: Literal["cpu", "gpu", "both", "requirement"]) -> dict[str, Any]:
    """Get JSON schema constrained to specific hardware type for LLM extraction.

    Args:
        hardware_type: "cpu" · "gpu" · "both" 는 데이터시트 방향,
                       "requirement" 는 규격서 방향.

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
    elif hardware_type == "requirement":
        return _llm_facing_requirement_schema()
    else:
        return HardwareExtraction.model_json_schema()
