#!/usr/bin/env python3
"""module_b 추출기 — LLM 없이 확인할 수 있는 것 전부.

실제 모델 호출은 `smoke_llm.py` 소관이다. 여기서는 **모델이 없어도 깨질 수 있는 것**만 본다:
스키마의 아귀, 근거 좌표 찾기, 공용 어휘 매핑, 프롬프트가 지켜야 할 문장.

프롬프트 문구를 테스트하는 게 유별나 보이지만, 여기 걸린 규칙 하나가 빠지면
(예: "제품을 추측하지 마라") 뒤 단계의 탐색이 지어낸 이름을 사실로 굳힌다.
조용히 틀리는 쪽이라 테스트로 묶어 둔다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from module_b.hardware_schema import (  # noqa: E402
    ATTR_UNITS,
    CPUInfo,
    GPUInfo,
    RequirementExtraction,
    RequirementItem,
    canonical_attrs,
    get_extraction_schema,
)
from module_b.llm_extractor import LLMExtractor, _locate, _surviving_quotes  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


# ── 근거 좌표 ─────────────────────────────────────────────────────────────────
print("근거 좌표(_locate)")

SOURCE = "1. 규격\n  가. GPU: 메모리 96GB 이상, CUDA 코어 24064개 이상\n  나. 저장장치: 24TB 이상"

span = _locate("가. GPU: 메모리 96GB 이상, CUDA 코어 24064개 이상", SOURCE)
check("원문 그대로면 찾는다", span is not None and span.found)
check(
    "좌표가 원문을 정확히 가리킨다",
    span is not None and SOURCE[span.offset : span.offset + span.length] == span.quote,
    f"got={SOURCE[span.offset:span.offset + span.length]!r}" if span else "",
)

# 모델이 표를 옮기며 공백을 다르게 쓰는 일은 흔하다 — 지어낸 것이 아니다.
wrapped = _locate("가. GPU:  메모리 96GB 이상,\nCUDA 코어 24064개 이상", SOURCE)
check("공백만 다르면 찾는다", wrapped is not None and wrapped.found)
check(
    "공백 무시로 찾아도 좌표는 원문 기준이다",
    wrapped is not None
    and SOURCE[wrapped.offset : wrapped.offset + wrapped.length].startswith("가. GPU"),
    f"got={SOURCE[wrapped.offset:wrapped.offset + wrapped.length]!r}" if wrapped else "",
)

invented = _locate("나. GPU: NVIDIA H200 141GB 3장", SOURCE)
check("지어낸 문장은 found=False", invented is not None and not invented.found)
check("지어낸 문장도 버리지 않고 싣는다", invented is not None and invented.quote != "")
check("빈 근거는 None", _locate("   ", SOURCE) is None)

kept = _surviving_quotes({"memory_size": "메모리 96GB 이상", "tdp": "TDP 700W"}, SOURCE)
check("확인되는 인용만 남는다", kept == {"memory_size": "메모리 96GB 이상"}, f"got={kept}")


# ── 스키마 ────────────────────────────────────────────────────────────────────
print("\n스키마")

item = RequirementItem(category="GPU", name="  ", named=True, evidence="x")
check("이름이 비면 named 가 접힌다", item.name is None and item.named is False)

named_by_model = RequirementItem(category="GPU", name="H200", named=False, evidence="x")
check(
    "이름은 있는데 named=False 는 그대로 둔다",
    named_by_model.name == "H200" and named_by_model.named is False,
)

parsed = RequirementExtraction.model_validate(
    {
        "items": [
            {
                "category": "HDD",
                "name": None,
                "named": False,
                "qty": 4,
                "constraints": [
                    {"attr": "capacity_gb", "op": "gte", "value": 24000, "unit": "GB", "raw": "24TB 이상"}
                ],
                "evidence": "나. 저장장치: 24TB 이상",
            }
        ]
    }
)
check("사양만 있는 품목이 통과한다", parsed.items[0].name is None)
check("연산자가 보존된다", parsed.items[0].constraints[0].op == "gte")
check("qty 가 보존된다", parsed.items[0].qty == 4)

# 규격서는 ECC 를 "R/E"·"ECC"·"지원" 으로 제각각 쓴다. 표기를 남기면 대조 쪽이 추론한다.
flags = RequirementExtraction.model_validate(
    {"items": [{"category": "RAM", "named": False, "evidence": "Memory: 64GB DDR5 R/E x32",
                "constraints": [
                    {"attr": "ecc", "op": "eq", "value": "R/E", "raw": "R/E"},
                    {"attr": "ecc", "op": "eq", "value": "미지원", "raw": "Non-ECC"},
                    {"attr": "memory_type", "op": "eq", "value": "DDR5", "raw": "DDR5"},
                ]}]}
).items[0].constraints
check("ecc 표기가 참으로 접힌다", flags[0].value is True, f"got={flags[0].value!r}")
check("ecc 미지원이 거짓으로 접힌다", flags[1].value is False, f"got={flags[1].value!r}")
check("다른 속성의 문자열은 그대로 둔다", flags[2].value == "DDR5")

check("빈 목록이 허용된다", RequirementExtraction.model_validate({"items": []}).items == [])

schema = get_extraction_schema("requirement")
defs = schema.get("$defs", {})
check("requirement 스키마에 품목 정의가 있다", "RequirementItem" in defs)
check(
    "서버가 채우는 필드는 LLM 스키마에서 빠진다",
    all("evidence_span" not in d.get("properties", {}) for d in defs.values()),
)
check(
    "서버가 채우는 필드가 required 에도 없다",
    all("evidence_span" not in d.get("required", []) for d in defs.values()),
)

# 데이터시트 방향은 기존 계약을 그대로 지켜야 한다 — CSV 적재가 여기에 걸려 있다.
for spec_type in ("cpu", "gpu"):
    keys = set(get_extraction_schema(spec_type))
    check(f"{spec_type} 스키마 모양 유지", keys == {"type", "properties", "required"}, f"got={keys}")


# ── 공용 어휘 ─────────────────────────────────────────────────────────────────
print("\n공용 어휘(canonical_attrs)")

gpu = GPUInfo(name="RTX PRO 6000", memorySize=96, shaders=24064, tdp=600)
attrs = canonical_attrs(gpu)
check("VRAM 이 memory_size_gb 로 옮겨진다", attrs.get("memory_size_gb") == 96)
check("shaders 가 그대로 온다", attrs.get("shaders") == 24064)
check("tdp 가 tdp_w 로 옮겨진다", attrs.get("tdp_w") == 600)
check("없는 값은 넣지 않는다", "memory_bandwidth_gbs" not in attrs)

cpu = CPUInfo(name="EPYC 9634", cores=84, ecc_support="Yes", memory_types="DDR5")
cpu_attrs = canonical_attrs(cpu)
check("ECC 는 참·거짓으로 옮겨진다", cpu_attrs.get("ecc") is True)
check("memory_types 가 memory_type 으로 옮겨진다", cpu_attrs.get("memory_type") == "DDR5")

check(
    "옮겨진 이름은 모두 공용 어휘에 있다",
    set(attrs) <= set(ATTR_UNITS) and set(cpu_attrs) <= set(ATTR_UNITS),
    f"unknown={(set(attrs) | set(cpu_attrs)) - set(ATTR_UNITS)}",
)


# ── 프롬프트 ──────────────────────────────────────────────────────────────────
print("\n프롬프트")

extractor = LLMExtractor.__new__(LLMExtractor)  # HTTP 클라이언트 없이 프롬프트만 본다

req_prompt = extractor._build_requirement_prompt(["가. GPU: 메모리 96GB 이상"])
for rule, why in [
    ("제품을 추측하지 마라", "이게 빠지면 탐색이 지어낸 이름을 사실로 굳힌다"),
    ("원문 한 줄을 그대로", "근거가 요약되면 백엔드 대조가 전부 기각된다"),
    ("gte", "연산자 어휘를 안 주면 '이상'이 사라진다"),
    ("prebuilt", "설계 ③ 완제품 표시"),
    ("named", "설계 ①·② 를 가르는 스위치"),
    ("children", "SET 을 평평하게 펴면 수량이 어긋난다"),
    ("capacity_gb", "카테고리별 어휘를 안 주면 RAM 이 GPU VRAM 자리를 쓴다"),
    ("\"named\":true", "①·② 가 갈리는 보기가 없으면 제품명을 놓친다"),
]:
    check(f"규격서 프롬프트에 '{rule}'", rule in req_prompt, f"— {why}")

check("규격서 프롬프트에 원문이 실린다", "가. GPU: 메모리 96GB 이상" in req_prompt)
check("허용 attr 목록이 실린다", "memory_size_gb" in req_prompt)
check("카테고리 목록이 실린다", "완제품" in req_prompt)

ds_prompt = extractor._build_prompt("gpu", ["Memory Size 96 GB"])
check("데이터시트 프롬프트에 인용 요구", "quotes" in ds_prompt)
check("데이터시트 프롬프트에 단위 환산 규칙", "24576 MB" in ds_prompt)
check("데이터시트 프롬프트에 추론 금지", "Never infer" in ds_prompt)
check("target 없으면 첫 제품 규칙", "first fully specified" in ds_prompt)

targeted = extractor._build_prompt("gpu", ["…"], target="RTX PRO 6000")
check("target 이 있으면 그 제품을 지목한다", 'Extract "RTX PRO 6000"' in targeted)


print()
if FAILURES:
    print(f"실패 {len(FAILURES)}건: {', '.join(FAILURES)}")
    sys.exit(1)
print("전부 통과")
