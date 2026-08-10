"""가격 조회 — 다나와·에누리·아이티마야를 하나의 동시(concurrent) 리졸버 뒤에 묶는다.

**LLM 이 관여하지 않는다** — 가격이 처음부터 정수로 나온다. 계약 전문은
`docs/backend-price-api.md`, 설계 근거는 `docs/price-search.md`.

경계 두 가지를 지킨다:

1. **우리는 후보(`quotes[]`)만 준다. 검증·선택은 백엔드 소유다**(§4). 요청 사양과 상품명을
   대조해 최저가를 고르는 것은 순수 규칙이라 `ai.enabled=false` 에서도 살아야 하기 때문이다.
2. **완화 검색이어도 `result=null` 을 우리가 만들지 않는다**(§8). `relaxed` 를 보고할 뿐이다.

소스는 `config.price_sources()`(env `PRICE_SOURCES`, 기본 "danawa,enuri,itmaya")로 켜고 끈다.
각 소스는 같은 데드라인으로 동시에 돌고, 결과를 소스 태그와 함께 합쳐 (source, sourceId) 로
중복을 제거한 뒤 소스 공평 라운드로빈으로 MAX_MERGED_QUOTES 까지 자른다.

실패 의미(Contract A / errors.py):
  - 0건 + 모든 소스가 전송/파싱 오류 ⇒ PRICE_SOURCE_BROKEN(502, 재시도 가능).
  - 0건 + 한 소스라도 깨끗이 검색됨 ⇒ 200 status="not-found".
  - 켜진 소스가 하나도 없음 ⇒ 200 status="no-source"(에러 아님).
  - 한 소스가 실패하고 다른 소스가 성공 ⇒ 200 + degraded:true + degradedReasons 채움.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import re
from urllib.parse import parse_qs, quote, urlparse  # noqa: F401 (quote: 하위호환 재노출)

from . import config, enuri, itmaya
from .errors import FAILURES, AiFailure
from .pricecommon import (
    MAX_MERGED_QUOTES,
    MAX_QUOTES,
    clean_html,
    deadline_seconds,
    fetch,
    make_quote,
    search_info,
)

DANAWA_SEARCH = "https://search.danawa.com/dsearch.php"
DANAWA_PRODUCT = "https://prod.danawa.com/info/"
SOURCE = "danawa"
_DEADLINE_CAP = 8.0

# 상품 블록 분리와 필드 추출. 다나와 검색 결과의 각 상품은 id="productItemNNN" 로 시작한다.
_ITEM_SPLIT = re.compile(r'id="productItem(\d+)"')
_NAME = re.compile(r'class="prod_name">\s*<a[^>]*>(.*?)</a>', re.S)
_PRICE = re.compile(r'class="price_sect"[^>]*>.*?<strong>([\d,]+)</strong>', re.S)
_SPEC = re.compile(r'class="spec_list"[^>]*>(.*?)</(?:div|dl)>', re.S)
_OG_TITLE = re.compile(r'property="og:title"\s+content="([^"]+)"')


# ── 다나와 소스 ──────────────────────────────────────────────────────────────
def _og_title(html: str) -> str:
    """상세 페이지의 og:title 에서 상품명. 다나와는 '[다나와] ' 접두를 붙인다."""
    match = _OG_TITLE.search(html)
    if not match:
        return ""
    return html_lib.unescape(match.group(1)).replace("[다나와]", "").strip()


def _parse_products(html: str) -> list[dict]:
    """검색 결과 HTML → 상품 후보. 파싱이 통째로 깨지면 호출부가 PRICE_SOURCE_BROKEN 으로 올린다.

    상품 블록(productItem) 단위로 잘라 각 조각 안에서 이름·가격을 짝짓는다. 전체 문서에
    정규식을 한 번에 걸면 이름과 가격의 순서가 어긋나 엉뚱한 값이 붙는다.
    """
    parts = _ITEM_SPLIT.split(html)
    # split 결과: [머리말, pcode1, 블록1, pcode2, 블록2, ...]
    quotes: list[dict] = []
    for i in range(1, len(parts) - 1, 2):
        pcode = parts[i]
        block = parts[i + 1]
        name_match = _NAME.search(block)
        price_match = _PRICE.search(block)
        if not name_match or not price_match:
            continue
        name = clean_html(name_match.group(1))
        price = int(price_match.group(1).replace(",", ""))
        if not name or price <= 0:
            continue
        spec_match = _SPEC.search(block)
        spec = clean_html(spec_match.group(1))[:300] if spec_match else ""
        quotes.append(make_quote(
            source=SOURCE, sourceId=pcode, url=f"{DANAWA_PRODUCT}?pcode={pcode}",
            name=name, priceKrw=price, spec=spec, basis="listed"))
        if len(quotes) >= MAX_QUOTES:
            break
    return quotes


async def search_danawa(item_name: str, deadline_ms: int | None = None) -> list[dict]:
    """다나와 검색 후보. 품목명은 가공하지 않고 원문 그대로 보낸다(문서 §8)."""
    deadline = deadline_seconds({"deadlineMs": deadline_ms}, _DEADLINE_CAP)
    html = await fetch(DANAWA_SEARCH, {"query": item_name}, deadline)
    try:
        return _parse_products(html)
    except AiFailure:
        raise
    except Exception as error:  # noqa: BLE001 — 파싱이 깨진 것은 사이트 개편 신호다
        raise AiFailure("PRICE_SOURCE_BROKEN", detail=f"danawa parse failed: {error}") from error


# ── 소스 레지스트리 ──────────────────────────────────────────────────────────
SOURCES: list[tuple[str, object]] = [
    ("danawa", search_danawa),
    ("enuri", enuri.search),
    ("itmaya", itmaya.search),
]


def _active_sources() -> list[tuple[str, object]]:
    enabled = set(config.price_sources())
    return [(name, fn) for name, fn in SOURCES if name in enabled]


# ── 병합 도우미 ──────────────────────────────────────────────────────────────
def _classify(exc: BaseException) -> tuple[str, str]:
    """소스 예외 → (code, 화면용 한국어 메시지). 메시지는 errors.py 상수에서만 온다(§2-9)."""
    if isinstance(exc, AiFailure):
        return exc.code, exc.spec.message
    return "PRICE_SOURCE_BROKEN", FAILURES["PRICE_SOURCE_BROKEN"].message


def _dedup(quotes: list[dict]) -> list[dict]:
    """같은 (source, sourceId) 를 한 번만. 순서는 유지한다."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for q in quotes:
        key = (q.get("source"), q.get("sourceId"))
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def _round_robin(quotes: list[dict], limit: int) -> list[dict]:
    """소스 공평 라운드로빈 절단 — 한 소스가 상한을 독식하지 않게 소스별로 한 개씩 번갈아 담는다."""
    buckets: dict[str, list[dict]] = {}
    order: list[str] = []
    for q in quotes:
        s = q.get("source")
        if s not in buckets:
            buckets[s] = []
            order.append(s)
        buckets[s].append(q)
    out: list[dict] = []
    idx = 0
    while len(out) < limit:
        progressed = False
        for s in order:
            bucket = buckets[s]
            if idx < len(bucket):
                out.append(bucket[idx])
                progressed = True
                if len(out) >= limit:
                    break
        if not progressed:
            break
        idx += 1
    return out


def _envelope(quotes: list[dict], info: dict, degraded: bool, degraded_reasons: list[dict]) -> dict:
    return {
        "quotes": quotes,
        "searchInfo": info,
        "degraded": degraded,
        "degradedReasons": degraded_reasons,
    }


async def resolve(payload: dict, *, sources: list[tuple[str, object]] | None = None) -> dict:
    """`POST /api/price/resolve` — 품목명으로 여러 소스에서 가격 후보를 동시에 찾는다.

    검증·선택은 하지 않는다(백엔드 소유). `sources` 를 주면(테스트용) 레지스트리 대신 그것을 쓴다.
    """
    item_name = str((payload or {}).get("itemName") or "").strip()
    if not item_name:
        raise AiFailure("BAD_REQUEST", detail="itemName 필수")

    deadline_ms = (payload or {}).get("deadlineMs")
    active = list(sources) if sources is not None else _active_sources()
    if not active:
        # 켜진 소스가 없다 = 적용 대상 없음. 에러가 아니라 no-source 200.
        return _envelope([], search_info(item_name, 0, "no-source"), False, [])

    outcomes = await asyncio.gather(
        *(fn(item_name, deadline_ms) for _name, fn in active),
        return_exceptions=True,
    )

    collected: list[dict] = []
    degraded_reasons: list[dict] = []
    misses: list[dict] = []
    raised = 0
    for (name, _fn), outcome in zip(active, outcomes):
        if isinstance(outcome, BaseException):
            raised += 1
            code, message = _classify(outcome)
            degraded_reasons.append({"source": name, "code": code, "message": message})
            misses.append({"site": name, "reason": code})
            continue
        rows = outcome or []
        if not rows:
            misses.append({"site": name, "reason": "not-found"})
        collected.extend(rows)

    quotes = _dedup(collected)

    if not quotes:
        if raised == len(active):
            # 모든 소스가 전송/파싱 단계에서 깨졌다 — "검색은 됐는데 0건" 과 다른 사건이다.
            raise AiFailure("PRICE_SOURCE_BROKEN", detail=f"all {len(active)} price sources failed")
        status = "not-found"
    else:
        status = "found"

    trimmed = _round_robin(quotes, MAX_MERGED_QUOTES)
    # degraded = 200 을 돌려주지만 최소 한 소스가 깨졌다(다른 소스가 성공/깨끗한 경우).
    degraded = bool(degraded_reasons)
    return _envelope(
        trimmed,
        search_info(item_name, len(trimmed), status, misses=misses),
        degraded,
        degraded_reasons,
    )


# ── URL 지목 조회 ────────────────────────────────────────────────────────────
async def _danawa_by_pcode(pcode: str, deadline_ms: int | None) -> list[dict]:
    """상세 페이지는 가격을 JS 로 그려 정적 파싱이 안 된다. 대신 상품명(og:title)을 읽어
    그 이름으로 검색하면 최저가가 검색 결과의 블록 구조로 나온다 — pcode 로 매칭한다."""
    deadline = deadline_seconds({"deadlineMs": deadline_ms}, _DEADLINE_CAP)
    detail = await fetch(DANAWA_PRODUCT, {"pcode": pcode}, deadline)
    product_name = _og_title(detail)
    query = product_name or pcode
    html = await fetch(DANAWA_SEARCH, {"query": query}, deadline)
    try:
        parsed = _parse_products(html)
    except AiFailure:
        raise
    except Exception as error:  # noqa: BLE001
        raise AiFailure("PRICE_SOURCE_BROKEN", detail=f"danawa parse failed: {error}") from error
    # 지목한 pcode 가 결과에 있으면 그것만, 없으면(개편·품절) 같은 이름의 후보를 그대로 준다.
    exact = [q for q in parsed if q["sourceId"] == pcode]
    return exact if exact else parsed


async def resolve_by_url(payload: dict) -> dict:
    """`POST /api/price/url` — 상품 URL 하나를 지목해 가격을 읽는다.

    §4.5(a) 화이트리스트: 다나와 `pcode` URL 과 에누리 `modelno` URL 만 지원한다. 임의 URL 을
    받아 fetch 하면 SSRF 표면이 열리고, 어떤 쇼핑몰이든 정확히 파싱한다는 보장도 없다.
    """
    url = str((payload or {}).get("url") or "").strip()
    if not url:
        raise AiFailure("BAD_REQUEST", detail="url 필수")
    deadline_ms = (payload or {}).get("deadlineMs")

    pcode = _danawa_pcode(url)
    if pcode is not None:
        quotes = await _danawa_by_pcode(pcode, deadline_ms)
        status = "found" if quotes else "not-found"
        return _envelope(quotes, search_info(pcode, len(quotes), status), False, [])

    modelno = enuri.enuri_modelno(url)
    if modelno is not None:
        quotes = await enuri.resolve_url(url, deadline_ms)
        status = "found" if quotes else "not-found"
        return _envelope(quotes, search_info(modelno, len(quotes), status), False, [])

    raise AiFailure("UNSUPPORTED_SOURCE", detail=url)


def _danawa_pcode(url: str) -> str | None:
    """다나와 상품 URL 에서 pcode 를 뽑는다. 다나와가 아니면 None."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if not parsed.hostname or "danawa.com" not in parsed.hostname.lower():
        return None
    pcode = parse_qs(parsed.query).get("pcode", [None])[0]
    return pcode if pcode and pcode.isdigit() else None


if __name__ == "__main__":  # 파싱 + 집계를 오프라인 검증한다(네트워크 없이)
    # ── 다나와 파싱(기존 검증 유지) ─────────────────────────────────────────
    sample = (
        'x id="productItem123"><p class="prod_name"><a href="#">RTX 5090 32GB</a></p>'
        '<p class="price_sect"><a><strong>7,499,000</strong></a></p>'
        'id="productItem456"><p class="prod_name"><a>노트북</a></p>'
        '<p class="price_sect"><strong>1,200,000</strong></p>'
    )
    parsed = _parse_products(sample)
    assert len(parsed) == 2, parsed
    assert parsed[0]["sourceId"] == "123" and parsed[0]["priceKrw"] == 7499000
    assert parsed[0]["name"] == "RTX 5090 32GB" and parsed[0]["basis"] == "listed"
    assert parsed[0]["source"] == "danawa" and parsed[0]["stale"] is False
    assert _danawa_pcode("https://prod.danawa.com/info/?pcode=103453760") == "103453760"
    assert _danawa_pcode("https://coupang.com/x?pcode=1") is None
    assert _danawa_pcode("https://danawa.com/info/?nocode=1") is None

    # ── 집계: 인메모리 가짜 소스로 세 갈래를 검증 ──────────────────────────────
    async def _empty(item_name, deadline_ms=None):
        return []

    async def _boom(item_name, deadline_ms=None):
        raise AiFailure("PRICE_SOURCE_BROKEN", detail="boom")

    async def _one(item_name, deadline_ms=None):
        return [make_quote(source="danawa", sourceId="1", url="https://x", name="X", priceKrw=1000)]

    async def _checks() -> None:
        # 모두 빈 검색 → not-found, degraded False
        r = await resolve({"itemName": "x"}, sources=[("danawa", _empty), ("enuri", _empty)])
        assert set(r) == {"quotes", "searchInfo", "degraded", "degradedReasons"}, r
        assert r["searchInfo"]["status"] == "not-found" and r["quotes"] == []
        assert r["degraded"] is False and r["degradedReasons"] == []

        # 한 소스 실패 + 한 소스 결과 → found, degraded True + degradedReasons
        r = await resolve({"itemName": "x"}, sources=[("danawa", _one), ("enuri", _boom)])
        assert r["searchInfo"]["status"] == "found" and len(r["quotes"]) == 1
        assert r["degraded"] is True
        assert len(r["degradedReasons"]) == 1 and r["degradedReasons"][0]["source"] == "enuri"
        assert r["degradedReasons"][0]["code"] == "PRICE_SOURCE_BROKEN"
        assert r["degradedReasons"][0]["message"]

        # 모든 소스 실패 → AiFailure PRICE_SOURCE_BROKEN
        try:
            await resolve({"itemName": "x"}, sources=[("danawa", _boom), ("enuri", _boom)])
            raise AssertionError("모든 소스 실패는 PRICE_SOURCE_BROKEN 을 올려야 한다")
        except AiFailure as error:
            assert error.code == "PRICE_SOURCE_BROKEN", error.code

        # 켜진 소스 없음 → no-source(에러 아님)
        r = await resolve({"itemName": "x"}, sources=[])
        assert r["searchInfo"]["status"] == "no-source" and r["degraded"] is False

        # 빈 itemName → BAD_REQUEST
        try:
            await resolve({"itemName": ""})
            raise AssertionError("빈 itemName 은 BAD_REQUEST 여야 한다")
        except AiFailure as error:
            assert error.code == "BAD_REQUEST", error.code

        # 라운드로빈: 소스 공평 절단
        many_a = [make_quote(source="a", sourceId=str(i), url="u", name="n", priceKrw=1) for i in range(40)]
        many_b = [make_quote(source="b", sourceId=str(i), url="u", name="n", priceKrw=1) for i in range(40)]
        trimmed = _round_robin(many_a + many_b, MAX_MERGED_QUOTES)
        assert len(trimmed) == MAX_MERGED_QUOTES
        assert sum(1 for q in trimmed if q["source"] == "a") == MAX_MERGED_QUOTES // 2

    asyncio.run(_checks())
    print("app/price.py: OK")
