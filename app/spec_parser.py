"""규격서 사양 문구 → 기계가 비교할 조건. 원본 `lib/spec-parser.js` 이식.

  "12Core 이상"   → {"cores": {"min": 12}}
  "TDP 150W 이하" → {"tdp": {"max": 150}}
  "GPU 메모리 24GB 이상" → {"vram": {"min": 24}}

왜 필요한가: 쇼핑몰에 "12Core 이상"이라는 상품은 없다. 사양은 살 물건이 아니라 물건을 고르는
**조건**이다. 먼저 조건으로 바꿔 두면, 다나와 검색 결과에서 "다른 용량·다른 계열·완제품"을
걸러낼 수 있다(RAM 요청에 데스크탑이 매칭되던 문제를 잡는다).

순수 함수다 — 파일도 네트워크도 모른다. self-check(`python -m app.spec_parser`)로 전부 검증된다.
"""

from __future__ import annotations

import re

# 부품 종류. GPU 를 먼저 본다 — "CUDA 코어"의 '코어'가 CPU 로 분류되지 않게.
_KINDS = [
    ("gpu", re.compile(r"\bGPU\b|그래픽\s*카드|\bVGA\b|\bVRAM\b|RTX|GTX|RADEON|QUADRO|TESLA|지포스|엔비디아|NVIDIA", re.I)),
    ("cpu", re.compile(r"\bCPU\b|프로세서|processor|코어|(?<![a-z])cores?\b|라이젠|ryzen|xeon|제온|epyc|스레드|쓰레드|(?<![a-z])threads?\b", re.I)),
    ("ram", re.compile(r"메모리|\bRAM\b|\bDIMM\b|\bDDR\d", re.I)),
    ("storage", re.compile(r"\bSSD\b|\bNVMe\b|\bHDD\b|저장\s*장치|하드\s*디스크", re.I)),
]

_AT_LEAST = re.compile(r"이상|초과|최소|\bmin\b", re.I)
_AT_MOST = re.compile(r"이하|미만|이내|최대|\bmax\b", re.I)
_LOOKAHEAD = 14
_LOOKBEHIND = 10

# (field, 기본 bound, 정규식, TB→GB 환산 여부)
_RULES = [
    ("cores", "min", re.compile(r"(\d{1,3})\s*(?:코어|cores?\b)", re.I), False),
    ("cores", "min", re.compile(r"(?:코어|cores?)\s*(\d{1,3})\s*개?", re.I), False),
    ("threads", "min", re.compile(r"(\d{1,3})\s*(?:스레드|쓰레드|threads?\b)", re.I), False),
    ("threads", "min", re.compile(r"(?:스레드|쓰레드|threads?)\s*(\d{1,3})\s*개?", re.I), False),
    ("boostClock", "min", re.compile(r"(?:부스트|터보|boost|turbo)\s*(?:클럭|clock)?\s*:?\s*(\d(?:\.\d+)?)\s*GHz", re.I), False),
    ("baseClock", "min", re.compile(r"(\d(?:\.\d+)?)\s*GHz", re.I), False),
    ("tdp", "max", re.compile(r"(?:TDP|소비\s*전력|열설계\s*전력)\s*:?\s*(\d{2,4})\s*W\b", re.I), False),
    ("vram", "min", re.compile(r"(?:VRAM|비디오\s*메모리|그래픽\s*메모리|GPU\s*메모리)\s*:?\s*(\d{1,3})\s*GB", re.I), False),
    ("memory", "min", re.compile(r"(?:메모리|\bRAM\b|\bDIMM\b)\s*(?:용량)?\s*:?\s*(\d{1,4})\s*(GB|TB)", re.I), True),
    ("memory", "min", re.compile(r"(\d{1,4})\s*(GB|TB)\s*(?:이상\s*)?(?:메모리|\bRAM\b|\bDIMM\b)", re.I), True),
]

_CORES_THREADS = re.compile(r"(\d{1,3})\s*C\s*/\s*(\d{1,3})\s*T\b", re.I)
_MEMORY_TYPE = re.compile(r"\b(?:LP)?G?DDR[3-7]X?\b", re.I)
_NON_ECC = re.compile(r"non-?\s*ECC|비\s*ECC", re.I)
_ECC = re.compile(r"\bECC\b", re.I)

_VENDORS = [
    ("Intel", re.compile(r"\bintel\b|인텔|제온|\bxeon\b|코어\s*i[3579]|core\s*i[3579]", re.I)),
    ("AMD", re.compile(r"\bAMD\b|라이젠|\bryzen\b|\bepyc\b|\bthreadripper\b", re.I)),
    ("NVIDIA", re.compile(r"\bnvidia\b|엔비디아|지포스|geforce|\brtx\b|\bgtx\b|quadro|tesla", re.I)),
]
_SERIES = [
    ("Xeon", re.compile(r"\bxeon\b|제온", re.I)),
    ("EPYC", re.compile(r"\bepyc\b", re.I)),
    ("Threadripper", re.compile(r"\bthreadripper\b|스레드리퍼", re.I)),
    ("Ryzen", re.compile(r"\bryzen\b|라이젠", re.I)),
    ("Core Ultra", re.compile(r"core\s*ultra|코어\s*울트라", re.I)),
    ("GeForce", re.compile(r"\bgeforce\b|지포스", re.I)),
    ("Quadro", re.compile(r"\bquadro\b", re.I)),
    ("Radeon", re.compile(r"\bradeon\b|라데온", re.I)),
]

_LABELS = {"cores": "코어", "threads": "스레드", "baseClock": "기본 클럭", "boostClock": "부스트 클럭",
           "tdp": "TDP", "vram": "VRAM", "memory": "메모리"}
_UNITS = {"cores": "개", "threads": "개", "baseClock": "GHz", "boostClock": "GHz",
          "tdp": "W", "vram": "GB", "memory": "GB"}


def _bound_of(text: str, start: int, end: int, fallback: str) -> str:
    after = text[end:end + _LOOKAHEAD]
    if _AT_MOST.search(after):
        return "max"
    if _AT_LEAST.search(after):
        return "min"
    before = text[max(0, start - _LOOKBEHIND):start]
    if _AT_MOST.search(before):
        return "max"
    if _AT_LEAST.search(before):
        return "min"
    return fallback


def detect_kind(text: str) -> str:
    for kind, rgx in _KINDS:
        if rgx.search(text):
            return kind
    return "unknown"


def parse_spec(text: str = "") -> dict:
    """사양 문구 → {kind, constraints, matched}. constraints 예: {cores:{min:12}, ecc:True}."""
    text = str(text or "")
    constraints: dict = {}
    matched: list[str] = []

    def put(field: str, bound: str, value) -> None:
        if not isinstance(value, (int, float)) or value <= 0:
            return
        if field in constraints:   # 먼저 잡힌 규칙이 더 구체적(규칙 순서 = 우선순위)
            return
        constraints[field] = {bound: value}
        num = int(value) if float(value).is_integer() else value
        matched.append(f"{_LABELS[field]} {num}{_UNITS[field]} {'이상' if bound == 'min' else '이하'}")

    ct = _CORES_THREADS.search(text)
    if ct:
        bound = _bound_of(text, ct.start(), ct.end(), "min")
        put("cores", bound, int(ct.group(1)))
        put("threads", bound, int(ct.group(2)))

    for field, default_bound, rgx, scale in _RULES:
        hit = rgx.search(text)
        if not hit:
            continue
        value = float(hit.group(1))
        if scale and (hit.lastindex or 0) >= 2 and str(hit.group(2) or "").upper() == "TB":
            value *= 1024
        put(field, _bound_of(text, hit.start(), hit.end(), default_bound), value)

    memory_types = sorted({t.upper() for t in _MEMORY_TYPE.findall(text)})
    if memory_types:
        constraints["memoryTypes"] = memory_types
        matched.append(f"메모리 규격 {'/'.join(memory_types)}")
    if _ECC.search(text) and not _NON_ECC.search(text):
        constraints["ecc"] = True
        matched.append("ECC 지원")
    for vendor, rgx in _VENDORS:
        if rgx.search(text):
            constraints["vendor"] = vendor
            matched.append(f"제조사 {vendor}")
            break
    for series, rgx in _SERIES:
        if rgx.search(text):
            constraints["series"] = series
            matched.append(f"계열 {series}")
            break

    return {"kind": detect_kind(text), "constraints": constraints, "matched": matched}


def has_constraints(parsed: dict) -> bool:
    return bool(parsed and parsed.get("constraints"))


def numeric_constraints(parsed: dict) -> dict:
    out = {}
    for field, bound in (parsed.get("constraints") or {}).items():
        if isinstance(bound, dict):
            out[field] = bound
    return out


if __name__ == "__main__":
    c = lambda s: parse_spec(s)["constraints"]  # noqa: E731
    assert c("12Core 이상")["cores"] == {"min": 12}
    assert c("코어 12개 이상")["cores"] == {"min": 12}
    assert c("최소 16코어")["cores"] == {"min": 16}
    assert c("TDP 150W 이하")["tdp"] == {"max": 150}
    assert c("TDP 150W")["tdp"] == {"max": 150}, "전력 기본 상한"
    assert c("코어 8개")["cores"] == {"min": 8}, "성능 기본 하한"
    assert c("64코어 이하")["cores"] == {"max": 64}
    assert c("12C/24T 이상")["cores"] == {"min": 12}
    assert c("12C/24T 이상")["threads"] == {"min": 24}
    assert "cores" not in c("보관 온도 40C"), "섭씨를 코어로 읽으면 안 된다"
    clk = c("기본 클럭 2.4GHz 이상, 부스트 클럭 5.0GHz 이상")
    assert clk["baseClock"] == {"min": 2.4} and clk["boostClock"] == {"min": 5.0}
    assert c("메모리 1TB 이상")["memory"] == {"min": 1024}
    assert c("GPU 메모리 24GB 이상")["vram"] == {"min": 24}
    assert c("DDR5 지원")["memoryTypes"] == ["DDR5"]
    assert c("ECC 지원 필수")["ecc"] is True
    assert "ecc" not in c("non-ECC 메모리"), "non-ECC 를 ECC 요구로 읽으면 안 된다"
    assert c("인텔 제온 16코어 이상")["series"] == "Xeon"
    assert c("AMD EPYC 32코어")["series"] == "EPYC"
    assert "series" not in c("CPU 12코어 이상")
    assert parse_spec("그래픽카드 CUDA 코어 10000개 이상")["kind"] == "gpu"
    assert parse_spec("DDR5 64GB 메모리")["kind"] == "ram"
    assert parse_spec("사무용 책상 1800x900")["kind"] == "unknown"
    assert has_constraints(parse_spec("12Core 이상")) is True
    assert has_constraints(parse_spec("사무용 책상")) is False
    assert numeric_constraints(parse_spec("12코어 이상 DDR5 ECC")) == {"cores": {"min": 12}}
    print("app/spec_parser.py: OK")
