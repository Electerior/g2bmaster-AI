"""에누리(enuri.com) 가격비교 스크레이퍼. price.py 의 다나와 구조를 그대로 따른다.

**LLM 이 관여하지 않는다** — 가격이 처음부터 정수로 나온다. 후보(quotes[])만 준다.

파싱 전략(견고성 순):
  1) JSON-LD (`<script type="application/ld+json">` 의 ItemList.itemListElement 또는 단독 Product).
     가장 안정적이라 있으면 먼저 쓴다.
  2) HTML 상품 블록 폴백 — 에누리 목록은 상품마다 model-no 링크·상품명·최저가를 담는다.

가드: HTML 이 실질적인데(non-trivial) 상품이 0건 파싱되면 조용히 [] 를 돌려주지 않고
PRICE_SOURCE_BROKEN 을 올린다 — 사이트 개편 날 모든 품목이 동시에 비는 장애 신호다. 단
"검색결과 없음" 마커가 보이면 그건 진짜 not-found 이므로 [] 를 돌려준다.

VERIFY LIVE: 아래 URL·CSS/JSON 선택자는 **라이브 에누리 페이지로 재확인이 필요**하다.
지금은 방어적으로 짜 두고, 오프라인 self-check 가 대표 샘플로 파싱 로직을 고정한다.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

from .errors import AiFailure
from .pricecommon import MAX_QUOTES, clean_html, deadline_seconds, fetch, make_quote

SOURCE = "enuri"

# ── VERIFY LIVE: URL·파라미터 ────────────────────────────────────────────────
ENURI_SEARCH = "https://www.enuri.com/search.jsp"
ENURI_DETAIL = "https://www.enuri.com/detail.jsp"
_SEARCH_PARAM = "keyword"
_DEADLINE_CAP = 8.0

# ── VERIFY LIVE: 파싱 선택자 ─────────────────────────────────────────────────
_JSONLD = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
#: 상품 블록의 시작 마커(컨테이너 <li>/<div> 의 data-modelno 속성, 상품당 한 번).
_ITEM_SPLIT = re.compile(r'data-modelno="(\d+)"', re.I)
#: 폴백: data-modelno 가 없으면 상세 링크의 modelno 로 자른다(상품당 여러 번일 수 있어 dedup 한다).
_ITEM_SPLIT_HREF = re.compile(r'modelno=(\d+)', re.I)
_NAME = re.compile(r'class="[^"]*\bprod_name\b[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>', re.S | re.I)
_PRICE = re.compile(r'class="[^"]*\bprice\b[^"]*"[^>]*>[^\d]*?([\d,]{3,})', re.S | re.I)
_HREF_MODELNO = re.compile(r'modelno=(\d+)|/(?:detail|model)/(\d+)', re.I)

#: 이름 꼬리표 제거 — "삼성 990 PRO 2TB - 에누리 가격비교" → "삼성 990 PRO 2TB".
_SUFFIX = re.compile(r"\s*-\s*에누리\s*가격비교\s*$")

#: 진짜 not-found 신호(장애가 아님). VERIFY LIVE.
_NO_RESULTS = re.compile(r"검색\s*결과가?\s*없|일치하는\s*상품이?\s*없|no\s+results?\s+found", re.I)
#: 이보다 큰 응답인데 0건이면 파서가 깨진 것으로 본다.
_NONTRIVIAL = 1500


def _strip_suffix(name: str) -> str:
    return _SUFFIX.sub("", name).strip()


def _to_int(value) -> int | None:
    """가격 문자열/숫자 → 양의 정수. 0·음수·해석불가는 None(모름, 절대 0 아님)."""
    if value is None:
        return None
    try:
        n = int(float(str(value).replace(",", "").strip()))
    except (ValueError, TypeError):
        return None
    return n if n > 0 else None


def _modelno_from_url(url: str) -> str | None:
    m = _HREF_MODELNO.search(str(url or ""))
    if not m:
        return None
    return m.group(1) or m.group(2)


def _detail_url(modelno: str) -> str:
    return f"{ENURI_DETAIL}?modelno={modelno}"


# ── JSON-LD ──────────────────────────────────────────────────────────────────
def _iter_products(data):
    """JSON-LD 노드 트리에서 Product dict 들을 뽑는다(ItemList·@graph·리스트·단독 Product)."""
    if isinstance(data, list):
        for node in data:
            yield from _iter_products(node)
        return
    if not isinstance(data, dict):
        return
    graph = data.get("@graph")
    if isinstance(graph, list):
        for node in graph:
            yield from _iter_products(node)
    items = data.get("itemListElement")
    if isinstance(items, list):
        for element in items:
            if isinstance(element, dict):
                item = element.get("item") if isinstance(element.get("item"), dict) else element
                if isinstance(item, dict):
                    yield item
        return
    types = data.get("@type")
    types = types if isinstance(types, list) else [types]
    if "Product" in types or isinstance(data.get("offers"), (dict, list)):
        yield data


def _extract_price(offers) -> int | None:
    if isinstance(offers, list):
        prices = [p for p in (_extract_price(o) for o in offers) if p is not None]
        return min(prices) if prices else None
    if isinstance(offers, dict):
        for key in ("lowPrice", "price"):
            n = _to_int(offers.get(key))
            if n is not None:
                return n
    return None


def _quote_from_product(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = _strip_suffix(clean_html(str(item.get("name") or "")))
    if not name:
        return None
    url = str(item.get("url") or "")
    sku = str(item.get("sku") or item.get("productID") or item.get("mpn") or "").strip()
    modelno = (sku if sku.isdigit() else "") or _modelno_from_url(url) or sku
    price = _extract_price(item.get("offers"))
    return make_quote(
        source=SOURCE,
        sourceId=modelno or name,
        url=url or (_detail_url(modelno) if modelno and modelno.isdigit() else ENURI_SEARCH),
        name=name,
        priceKrw=price,
        basis="listed",
    )


def _parse_jsonld(html: str) -> list[dict]:
    quotes: list[dict] = []
    seen: set[str] = set()
    for block in _JSONLD.finditer(html):
        try:
            data = json.loads(block.group(1).strip())
        except ValueError:
            continue
        for item in _iter_products(data):
            quote = _quote_from_product(item)
            if not quote:
                continue
            if quote["sourceId"] in seen:
                continue
            seen.add(quote["sourceId"])
            quotes.append(quote)
            if len(quotes) >= MAX_QUOTES:
                return quotes
    return quotes


# ── HTML 폴백 ────────────────────────────────────────────────────────────────
def _split_parse(html: str, splitter: re.Pattern) -> list[dict]:
    parts = splitter.split(html)
    quotes: list[dict] = []
    seen: set[str] = set()
    for i in range(1, len(parts) - 1, 2):
        modelno = parts[i]
        block = parts[i + 1]
        if modelno in seen:
            continue
        name_match = _NAME.search(block)
        if not name_match:
            continue
        name = _strip_suffix(clean_html(name_match.group(1)))
        if not name:
            continue
        price_match = _PRICE.search(block)
        price = _to_int(price_match.group(1)) if price_match else None
        seen.add(modelno)
        quotes.append(make_quote(source=SOURCE, sourceId=modelno, url=_detail_url(modelno),
                                 name=name, priceKrw=price, basis="listed"))
        if len(quotes) >= MAX_QUOTES:
            break
    return quotes


def _parse_html(html: str) -> list[dict]:
    quotes = _split_parse(html, _ITEM_SPLIT)
    if not quotes:
        quotes = _split_parse(html, _ITEM_SPLIT_HREF)
    return quotes


def _parse(html: str) -> list[dict]:
    """검색 결과 HTML → 상품 후보. JSON-LD 우선, HTML 폴백, 그리고 가드."""
    quotes = _parse_jsonld(html)
    if not quotes:
        quotes = _parse_html(html)
    if not quotes and len(html) >= _NONTRIVIAL and not _NO_RESULTS.search(html):
        # 실질 페이지인데 0건 = 선택자가 깨졌다는 신호. 조용히 [] 를 돌려주지 않는다.
        raise AiFailure("PRICE_SOURCE_BROKEN", detail="enuri: 0 products from non-trivial page")
    return quotes[:MAX_QUOTES]


# ── 공개 API ─────────────────────────────────────────────────────────────────
async def search(item_name: str, deadline_ms: int | None = None) -> list[dict]:
    """품목명으로 에누리 검색 후보를 뽑는다. 전송/파싱 실패는 AiFailure 로 올라간다."""
    deadline = deadline_seconds({"deadlineMs": deadline_ms}, _DEADLINE_CAP)
    html = await fetch(ENURI_SEARCH, {_SEARCH_PARAM: item_name}, deadline)
    return _parse(html)


def enuri_modelno(url: str) -> str | None:
    """에누리 상품 URL 에서 modelno 를 뽑는다. 에누리가 아니면 None(화이트리스트용)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if not parsed.hostname or "enuri.com" not in parsed.hostname.lower():
        return None
    qs = parse_qs(parsed.query)
    for key in ("modelno", "modelNo", "model_no"):
        val = qs.get(key, [None])[0]
        if val and val.isdigit():
            return val
    m = re.search(r"/(?:detail|model)/(\d+)", parsed.path)
    return m.group(1) if m else None


async def resolve_url(url: str, deadline_ms: int | None = None) -> list[dict]:
    """에누리 상세 URL 하나 → 후보. 상세 페이지는 대개 단독 Product JSON-LD 를 담는다.

    상세는 최선-노력이라 0건이어도 예외를 올리지 않는다(가격을 JS 로 그리면 정적 파싱 실패).
    """
    deadline = deadline_seconds({"deadlineMs": deadline_ms}, _DEADLINE_CAP)
    html = await fetch(url, None, deadline)
    quotes = _parse_jsonld(html)
    if not quotes:
        quotes = _parse_html(html)
    return quotes[:MAX_QUOTES]


if __name__ == "__main__":  # 네트워크 없이 대표 샘플로 파싱 로직만 고정한다(VERIFY LIVE 는 별도)
    jsonld = (
        '<html><head>'
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"ItemList","itemListElement":['
        '{"@type":"ListItem","position":1,"item":{"@type":"Product",'
        '"name":"삼성전자 990 PRO 2TB - 에누리 가격비교","sku":"12345678",'
        '"url":"https://www.enuri.com/detail.jsp?modelno=12345678",'
        '"offers":{"@type":"AggregateOffer","lowPrice":"289000","priceCurrency":"KRW"}}},'
        '{"@type":"ListItem","position":2,"item":{"@type":"Product",'
        '"name":"RTX 5090 그래픽카드","sku":"87654321",'
        '"url":"https://www.enuri.com/detail.jsp?modelno=87654321",'
        '"offers":{"@type":"Offer","price":"2750000"}}}'
        ']}</script></head><body>...</body></html>'
    )
    qs = _parse(jsonld)
    assert len(qs) == 2, qs
    assert qs[0]["source"] == "enuri" and qs[0]["basis"] == "listed" and qs[0]["stale"] is False
    assert qs[0]["sourceId"] == "12345678" and qs[0]["priceKrw"] == 289000
    assert qs[0]["name"] == "삼성전자 990 PRO 2TB", qs[0]["name"]     # 꼬리표 제거
    assert qs[0]["url"].endswith("modelno=12345678")
    assert qs[1]["sourceId"] == "87654321" and qs[1]["priceKrw"] == 2750000

    html = (
        '<html><body><ul class="prodList">'
        '<li class="prodItem" data-modelno="11112222">'
        '<p class="prod_name"><a href="/detail.jsp?modelno=11112222">LG 그램 17 - 에누리 가격비교</a></p>'
        '<p class="price lowest"><strong>1,890,000</strong>원</p></li>'
        '<li class="prodItem" data-modelno="33334444">'
        '<p class="prod_name"><a href="/detail.jsp?modelno=33334444">ASUS ROG</a></p>'
        '<p class="price"><em>2,450,000</em>원</p></li>'
        '</ul>' + "x" * 1600 + '</body></html>'
    )
    hq = _parse(html)
    assert len(hq) == 2, hq
    assert hq[0]["sourceId"] == "11112222" and hq[0]["priceKrw"] == 1890000
    assert hq[0]["name"] == "LG 그램 17" and hq[0]["url"].endswith("modelno=11112222")
    assert hq[1]["sourceId"] == "33334444" and hq[1]["priceKrw"] == 2450000

    # 가드: 실질 페이지인데 0건 → PRICE_SOURCE_BROKEN
    broken = "<html><body>" + "<div>레이아웃 조각</div>" * 300 + "</body></html>"
    try:
        _parse(broken)
        raise AssertionError("0건인데 PRICE_SOURCE_BROKEN 을 올려야 한다")
    except AiFailure as error:
        assert error.code == "PRICE_SOURCE_BROKEN", error.code

    # 검색결과 없음 마커 → 조용히 [](장애 아님)
    empty = "<html><body>" + "<div>x</div>" * 300 + "<p>검색결과가 없습니다</p></body></html>"
    assert _parse(empty) == [], "no-results 마커는 not-found 여야 한다"

    # 사소한 응답(선택자 미매치여도 가드 미발동)
    assert _parse("<html></html>") == []

    assert enuri_modelno("https://www.enuri.com/detail.jsp?modelno=12345678") == "12345678"
    assert enuri_modelno("https://prod.danawa.com/info/?pcode=1") is None
    assert enuri_modelno("https://coupang.com/x?modelno=9") is None
    print("app/enuri.py: OK")
