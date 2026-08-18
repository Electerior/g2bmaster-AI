"""다나와 검색 결과를 요청 부품의 정체성으로 검증한다. 원본 `lib/market-price-resolver.js` 이식.

부품 단가를 구할 때 "가장 싼 것"을 그냥 고르면 안 된다 — 완제품 PC·중고·렌탈·액세서리,
그리고 **다른 용량**(2TB 찾는데 512GB)이 최저가 자리에 앉는다. 그래서 검색 결과를 요청 부품의
정체성(모델·용량)과 대조해 어긋나는 것을 버린 뒤 최저가를 고른다.

핵심 두 함수:
  - derive_product_identity(name) → {model, specs, excludedKinds, strong}
  - quote_matches_identity(quote, identity) → {ok, reason}

순수 함수다. self-check(`python -m app.part_resolver`)로 검증된다.
"""

from __future__ import annotations

import re
import unicodedata

# 배제 종류는 **원문 제목**에서 판정한다 — 정규화하면 괄호가 사라져 "[16GB, M.2 1TB]" 같은
# 완제품 구성 표기를 잃는다. 패턴은 "이 상품이 무엇인가"만 겨냥한다(스치는 단어로 버리지 않음).
_KIND_PATTERNS = {
    "완제품 PC": [
        re.compile(r"완제품|조립\s*pc|게이밍\s*pc|사무용\s*pc|본체", re.I),
        re.compile(r"데스크[탑톱](?!\s*용)", re.I),                 # "데스크탑용 메모리"는 부품
        re.compile(r"[가-힣]\s*pc(?!\s*용)\b", re.I),
        re.compile(r"\[[^\]]*\b(?:m\.2|ssd|hdd)\b[^\]]*\]", re.I),   # 완성 시스템 구성 표기
        # CPU 모델 + 외장 GPU 모델을 한 제목에 = 시스템(브랜드명만으론 못 거른다)
        re.compile(r"(?=.*(?:라이젠|ryzen|코어\s*i|core\s*i|\br[3579]\s*-?\s*\d{4}|\bi[3579]\s*-?\s*\d{4,5}))"
                   r"(?=.*\b(?:rtx|gtx)\s*-?\s*\d{3,4})", re.I),
    ],
    "중고": [re.compile(r"중고|리퍼|리퍼비시|refurbished|\bused\b", re.I)],
    "렌탈": [re.compile(r"렌탈|임대|대여|리스(?![트크])", re.I)],
    "액세서리": [re.compile(r"액세서리|악세서리|거치대|브라켓|받침대|파우치|워터블럭|쿨러", re.I)],
}

_INCLUSION = re.compile(r"[([{][^)\]}]*(?:포함|미포함|증정|사은품|번들|동봉)[^)\]}]*[)\]}]")
_INCLUSION2 = re.compile(r"\S*\s*(?:미포함|포함|증정|동봉)")


def strip_inclusion_notes(title: str = "") -> str:
    """"(정품박스 쿨러포함)" 같은 구성품 안내 제거 — 이것 때문에 CPU 가 '액세서리'로 버려졌다."""
    t = _INCLUSION.sub(" ", str(title or ""))
    return _INCLUSION2.sub(" ", t)


def looks_like_kind(title: str, kind: str) -> bool:
    text = strip_inclusion_notes(title)
    return any(p.search(text) for p in _KIND_PATTERNS.get(kind, []))


def _normalize(value: str = "") -> str:
    v = unicodedata.normalize("NFKC", str(value)).lower()
    return re.sub(r"[^a-z0-9가-힣]+", " ", v).strip()


def _tokens(value: str = "") -> list[str]:
    return [t for t in _normalize(value).split() if t]


# 사양 대조: "어긋나면 탈락"(1TB 찾는데 512GB), 미표기면 보류(국내 제목은 용량 생략이 흔함).
_SPEC_DIMENSIONS = {
    "GB": ("용량", 1.0), "TB": ("용량", 1024.0), "MB": ("용량", 1 / 1024),
    "MHZ": ("주파수", 1.0), "GHZ": ("주파수", 1000.0), "W": ("전력", 1.0),
}
_SPEC_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*(GB|TB|MB|GHZ|MHZ|W)\b", re.I)
_SPEC_EXACT = re.compile(r"^(\d+(?:\.\d+)?)(GB|TB|MB|GHZ|MHZ|W)$")
_SPEC_TOLERANCE = 0.06   # 1TB 를 1024/1000 둘 다 쓴다 — 2.4% 차이를 다른 제품으로 보면 안 된다

#: 묶음 표기 — "768GB 12X64GB"·"256GB 4X64GB"·"64GB (2x32GB)". 가격이 **여러 개분**이다.
#: 단품 견적으로 쓰면 원가가 개수만큼 부풀려진다(실측: 64GB RDIMM 에 12개 묶음이
#: 단품처럼 들어와 총원가가 10배 근처로 치솟았다). 개수≥2 면 그 차원에서 거부한다.
_BUNDLE = re.compile(r"(\d+)\s*[xX×]\s*(\d+(?:\.\d+)?)\s*(GB|TB|MB)", re.I)


def _same_magnitude(a: float, b: float) -> bool:
    return abs(a - b) <= max(a, b) * _SPEC_TOLERANCE


def _conflicting_spec(raw_title: str, specs: list[str]) -> str:
    want: dict[str, list[tuple[float, str]]] = {}
    for spec in specs or []:
        m = _SPEC_EXACT.match(str(spec).upper().replace(" ", ""))
        if not m or m.group(2) not in _SPEC_DIMENSIONS:
            continue
        dim, factor = _SPEC_DIMENSIONS[m.group(2)]
        want.setdefault(dim, []).append((float(m.group(1)) * factor, f"{m.group(1)}{m.group(2)}"))
    if not want:
        return ""
    # 묶음(개수×용량) — 가격이 한 개분이 아니므로 어떤 차원에서든 바로 거부한다.
    for m in _BUNDLE.finditer(str(raw_title)):
        count = int(m.group(1))
        dim, _factor = _SPEC_DIMENSIONS[m.group(3).upper()]
        if count >= 2 and dim in want:
            return f"{m.group(0)} 묶음"
    seen: dict[str, list[float]] = {}
    for m in _SPEC_TOKEN.finditer(str(raw_title).upper()):
        dim, factor = _SPEC_DIMENSIONS[m.group(2).upper()]
        if dim in want:
            seen.setdefault(dim, []).append(float(m.group(1)) * factor)
    for dim, wanted in want.items():
        found = seen.get(dim)
        if not found:
            continue   # 제목이 그 차원 미표기 → 보류
        if not any(_same_magnitude(w[0], f) for w in wanted for f in found):
            return wanted[0][1]
        # 원하는 값이 있으면서 **더 큰 값도 함께** 있으면 묶음·총액 표기다 —
        # "512GB (8X64GB)" 의 X 표기를 놓친 변형("512GB DDR5 64GB")도 여기서 걸린다.
        # 가격이 여러 개분이므로 단품 견적으로 쓰면 원가가 개수만큼 부풀려진다.
        ceiling = max(w[0] for w in wanted) * (1 + _SPEC_TOLERANCE)
        oversized = [f for f in found if f > ceiling]
        if oversized:
            return "묶음 용량"
    return ""


_KO_RYZEN = re.compile(r"라이젠\s*[3579]?\s*[- ]?(\d{4}[A-Z0-9-]*)", re.I)
_EN_RYZEN = re.compile(r"\b(?:AMD\s*)?RYZEN\s*[3579]?\s*[- ]?(\d{4}[A-Z0-9-]*)\b", re.I)
_MANUF = re.compile(r"\b(NVIDIA|AMD|Intel|Samsung|LG|Apple|ASUS|MSI|Dell|HP|Lenovo|GIGABYTE|ASRock|WD|Seagate)\b", re.I)
_MODEL = re.compile(r"\b(RTX|GTX|RX|ARC|RYZEN|CORE|XEON|EPYC)\s*[- ]?([A-Z]?\d{3,5}[A-Z0-9-]*)\b", re.I)
_SPECS = re.compile(r"\b\d+(?:\.\d+)?\s*(?:GB|TB|MB|GHz|MHz|W)\b", re.I)
_GENERIC = re.compile(
    r"\b(?![0-9]+(?:\.[0-9]+)?(?:GB|TB|MB|KB|W|V|A|HZ|MHZ|GHZ|MM|CM|INCH)\b)"
    r"(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*[0-9])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b", re.I)


def derive_product_identity(query: str = "") -> dict:
    text = unicodedata.normalize("NFKC", str(query or "")).strip()
    ko = _KO_RYZEN.search(text)
    en = _EN_RYZEN.search(text)
    manuf = _MANUF.search(text)
    model = _MODEL.search(text)
    specs = list(dict.fromkeys(re.sub(r"\s+", "", m.group(0)).upper() for m in _SPECS.finditer(text)))
    generic = None if (model or ko or en) else _GENERIC.search(text)
    # 길이 4 미만 조각("1U"·"x4"·"5in")은 모델이 아니라 단위·수식 잔재다 — 모델로 승격시키면
    # 모든 견적이 "그 토큰 없음"으로 탈락한다(실측: SSD "2.5in" → 모델 "5IN" → 전멸).
    if generic is not None and len(generic.group(0)) < 4:
        generic = None
    model_str = (
        ko.group(1).upper() if ko
        else en.group(1).upper() if en
        else (model.group(1).upper() + " " + model.group(2).upper()) if model
        else generic.group(0).upper() if generic
        else "")
    return {
        "manufacturer": manuf.group(1) if manuf else ("AMD" if ko else ""),
        "model": model_str,
        "specs": specs,
        "excludedKinds": ["완제품 PC", "중고", "렌탈", "액세서리"],
        "query": text,
        "strong": bool(model or ko or en or generic),
    }


def quote_matches_identity(quote: dict, identity: dict) -> dict:
    """quote {title, price, url} 가 identity 와 맞는가. {ok, reason}."""
    raw_title = str((quote or {}).get("title") or "")
    title = _normalize(raw_title)
    if not title:
        return {"ok": False, "reason": "missing-title"}

    kind_text = strip_inclusion_notes(raw_title)
    for kind in identity.get("excludedKinds", []):
        if any(p.search(kind_text) for p in _KIND_PATTERNS.get(kind, [])):
            return {"ok": False, "reason": f"excluded-kind:{kind}"}

    required = list(dict.fromkeys(_tokens(identity.get("model", ""))))
    missing = [t for t in required if t not in title]
    if missing:
        return {"ok": False, "reason": f"identity-token-missing:{','.join(missing)}"}

    conflict = _conflicting_spec(raw_title, identity.get("specs", []))
    if conflict:
        return {"ok": False, "reason": f"spec-mismatch:{conflict}"}

    price = quote.get("price")
    if not isinstance(price, (int, float)) or price <= 0:
        return {"ok": False, "reason": "invalid-price"}
    if not re.match(r"^https?://", str(quote.get("url") or ""), re.I):
        return {"ok": False, "reason": "invalid-url"}
    return {"ok": True, "reason": ""}


if __name__ == "__main__":
    # 완제품/중고/다른용량 제외
    ssd = derive_product_identity("삼성 990 PRO 2TB")
    assert ssd["specs"] == ["2TB"], ssd
    assert quote_matches_identity({"title": "삼성 990 PRO 2TB M.2 NVMe", "price": 300000, "url": "https://x"}, ssd)["ok"]
    assert not quote_matches_identity({"title": "삼성 990 PRO 512GB", "price": 90000, "url": "https://x"}, ssd)["ok"], "다른 용량 제외"
    assert not quote_matches_identity({"title": "게이밍PC 라이젠 RTX 5070 완본체", "price": 1500000, "url": "https://x"}, ssd)["ok"], "완제품 제외"
    assert not quote_matches_identity({"title": "990 PRO 2TB [중고]", "price": 100000, "url": "https://x"}, ssd)["ok"], "중고 제외"
    # 모델 토큰 필수
    gpu = derive_product_identity("RTX 5090")
    assert gpu["model"] == "RTX 5090"
    assert quote_matches_identity({"title": "MSI 지포스 RTX 5090 슈프림 32GB", "price": 7000000, "url": "https://x"}, gpu)["ok"]
    assert not quote_matches_identity({"title": "MSI 지포스 RTX 5080", "price": 1500000, "url": "https://x"}, gpu)["ok"], "다른 모델 제외"
    # 용량 미표기는 보류(통과)
    assert quote_matches_identity({"title": "MSI 지포스 RTX 5090 슈프림", "price": 7000000, "url": "https://x"}, gpu)["ok"]
    # 묶음(개수×용량)은 단품 견적으로 받지 않는다 — 총원가 10배 사고의 원인(실측)
    ram = derive_product_identity("DDR5-5600 64GB RDIMM")
    assert not quote_matches_identity(
        {"title": "[해외] 768GB 12X64GB DDR5 5600MHz RDIMM", "price": 62271330, "url": "https://x"}, ram)["ok"], "12개 묶음 제외"
    assert not quote_matches_identity(
        {"title": "[해외] 256GB 4X64GB DDR5 5600MHz RDIMM", "price": 18961250, "url": "https://x"}, ram)["ok"], "4개 묶음 제외"
    assert not quote_matches_identity(
        {"title": "네믹스 램 512GB DDR5 5600MHz 64GB RDIMM", "price": 64154210, "url": "https://x"}, ram)["ok"], "총용량 표기 묶음 제외(X 표기 없는 변형)"
    assert quote_matches_identity(
        {"title": "[해외] 64GB 1X64GB DDR5 5600MHz ECC RDIMM", "price": 4749730, "url": "https://x"}, ram)["ok"], "1X64GB 는 단품"
    # 여러 용량 옵션을 한 제목에 적은 상품은 유지(옵션 나열은 묶음이 아니다)
    assert quote_matches_identity(
        {"title": "삼성 DDR5 16G 32G 64G 5600 RDIMM", "price": 439000, "url": "https://x"}, ram)["ok"], "옵션 나열은 통과"
    # 쿨러포함 안내가 CPU 를 액세서리로 만들면 안 된다
    cpu = derive_product_identity("라이젠 9800X3D")
    assert quote_matches_identity({"title": "AMD 라이젠7 9800X3D (정품박스 쿨러포함)", "price": 600000, "url": "https://x"}, cpu)["ok"]
    print("app/part_resolver.py: OK")
