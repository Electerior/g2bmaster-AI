"""가격 소스 공통 원시(primitive) 모음.

다나와·에누리·아이티마야 세 소스 모듈이 **서로를 import 하지 않고** 같은 계약을 지키도록
공유 조각을 여기 한 곳에 둔다. 의존은 `errors` + `httpx` 뿐이다(순환 없음).

여기서 정하는 계약(backend-price-api.md 반영):

  - Quote 정규형: {source, sourceId, url, name, priceKrw:int|None, spec, basis,
    collectedAt(ISO-8601 KST), stale:bool}. `priceKrw` 는 정수(KRW)이며 **모르면 None**,
    절대 0 이 아니다. `make_quote(...)` 가 유일한 생성 통로다.
  - searchInfo 봉투: {queryUsed, count, status, relaxed:false, relaxation?, misses:[{site,reason}]}.
    기존 봉투 호환을 위해 `quoteCount` 도 함께 싣는다(§8 이름변경 금지 — 새 필드만 추가).
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx

from .errors import AiFailure

#: 한 소스가 한 번에 돌려줄 후보 상한.
MAX_QUOTES = 20
#: 여러 소스를 합친 뒤 리졸버가 최종적으로 돌려줄 상한(소스 공평 라운드로빈으로 잘린다).
MAX_MERGED_QUOTES = 30

KST = timezone(timedelta(hours=9))

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) g2bmaster-ai/price"

#: 소스 데드라인 상한(초). AI 자체 데드라인 < 백엔드 타임아웃(config.BACKEND_TIMEOUT_SECONDS).
DEFAULT_DEADLINE_SECONDS = 8.0

_TAGS = re.compile(r"<[^>]+>")


def now_kst() -> str:
    """수집 시각(ISO-8601, KST, 초 단위). 예: 2026-08-07T19:09:55+09:00."""
    return datetime.now(KST).replace(microsecond=0).isoformat()


def clean_html(fragment: str) -> str:
    """HTML 조각에서 태그를 걷어내고 엔티티를 풀어 한 줄로 만든다(price.py 원본 이식)."""
    text = _TAGS.sub(" ", str(fragment or ""))
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def deadline_seconds(payload: dict | None, default: float = DEFAULT_DEADLINE_SECONDS) -> float:
    """요청의 deadlineMs → 초. 없거나 잘못되면 default. default 가 상한이기도 하다.

    AI 자체 데드라인이 백엔드 타임아웃보다 커지면 같은 작업을 두 번 추론한다(CLAUDE.md §2-7).
    """
    raw = (payload or {}).get("deadlineMs")
    if isinstance(raw, (int, float)) and raw > 0:
        return min(float(raw) / 1000.0, default)
    return default


async def fetch(url: str, params: dict | None = None, deadline_s: float = DEFAULT_DEADLINE_SECONDS,
                *, headers: dict | None = None) -> str:
    """공용 GET. price.py 원본 `_fetch` 와 **같은 실패 분류**를 올린다.

    타임아웃 → LLM_TIMEOUT(504, 재시도 가능), 그 외 전송/HTTP 오류 → PRICE_SOURCE_BROKEN
    (502, 재시도 가능). 둘 다 파싱 이전 단계다.
    """
    hdr = {"User-Agent": USER_AGENT}
    if headers:
        hdr.update(headers)
    try:
        async with httpx.AsyncClient(timeout=deadline_s, follow_redirects=True) as client:
            response = await client.get(url, params=params, headers=hdr)
            response.raise_for_status()
            return response.text
    except httpx.TimeoutException as error:
        raise AiFailure("LLM_TIMEOUT", detail=f"price fetch timeout: {url}") from error
    except httpx.HTTPError as error:
        raise AiFailure("PRICE_SOURCE_BROKEN", detail=f"{type(error).__name__}: {url}") from error


def make_quote(*, source: str, sourceId: str | int, url: str, name: str,
               priceKrw: int | None, spec: str = "", basis: str = "listed",
               collectedAt: str | None = None, stale: bool = False) -> dict:
    """Quote 정규형의 유일한 생성 통로.

    기본값: basis="listed"(그 URL 에 그 가격이 실제로 표시돼 있었다), stale=False,
    collectedAt=지금(KST). 오래된 파일 소스는 basis="stale", stale=True, collectedAt=파일 mtime.
    priceKrw 는 정수거나 None(모름) — 0 을 넣지 않는다.
    """
    return {
        "source": source,
        "sourceId": str(sourceId),
        "url": url,
        "name": name,
        "priceKrw": priceKrw,
        "spec": spec,
        "basis": basis,
        "collectedAt": collectedAt or now_kst(),
        "stale": stale,
    }


def search_info(query_used: str, count: int, status: str, *,
                misses: list[dict] | None = None, relaxation: dict | None = None) -> dict:
    """searchInfo 봉투. status ∈ {found, not-found, no-source}.

    `relaxed` 는 항상 False — 우리는 사양 필터를 걸지 않으므로 완화 검색이 없다(문서 §8).
    `count` 가 계약형이고, 기존 봉투 호환을 위해 `quoteCount` 도 같은 값으로 싣는다.
    """
    return {
        "status": status,
        "queryUsed": query_used,
        "relaxed": False,
        "relaxation": relaxation or {"droppedFilters": [], "notes": [], "textFallback": []},
        "count": count,
        "quoteCount": count,
        "misses": misses or [],
    }


class PriceSource(Protocol):
    """가격 소스 규약(설명용 Protocol).

    각 소스 모듈은 이 시그니처의 async 콜러블을 노출한다 — 리졸버의 SOURCES 레지스트리는
    (이름, search) 튜플을 담는다. search 는 make_quote() 로 만든 quote dict 리스트를
    돌려주거나, 회복 불가한 전송/파싱 오류일 때 AiFailure 를 올린다(조용히 [] 를 돌려주지 않는다).
    데드라인은 밀리초로 받는다(없으면 소스 기본 상한).
    """

    async def __call__(self, item_name: str, deadline_ms: int | None = None) -> list[dict]: ...


if __name__ == "__main__":  # 네트워크 없이 계약 모양만 검증한다
    q = make_quote(source="danawa", sourceId=123, url="https://x", name="RTX 5090", priceKrw=100)
    assert q["sourceId"] == "123" and q["basis"] == "listed" and q["stale"] is False
    assert q["priceKrw"] == 100 and q["spec"] == ""
    assert set(q) == {"source", "sourceId", "url", "name", "priceKrw", "spec", "basis", "collectedAt", "stale"}

    q2 = make_quote(source="itmaya", sourceId="GPU|ESC|3", url="https://y", name="H200",
                    priceKrw=None, basis="stale", stale=True, collectedAt="2026-01-01T00:00:00+09:00")
    assert q2["priceKrw"] is None and q2["stale"] is True and q2["basis"] == "stale"
    assert q2["collectedAt"] == "2026-01-01T00:00:00+09:00"

    info = search_info("RTX 5090", 2, "found")
    assert info["status"] == "found" and info["count"] == 2 and info["quoteCount"] == 2
    assert info["relaxed"] is False and info["queryUsed"] == "RTX 5090" and info["misses"] == []
    info2 = search_info("x", 0, "not-found", misses=[{"site": "enuri", "reason": "PRICE_SOURCE_BROKEN"}])
    assert info2["misses"][0]["site"] == "enuri"

    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00$", now_kst()), now_kst()
    assert clean_html("<b>A</b>&nbsp; B  C") == "A B C"
    assert deadline_seconds({"deadlineMs": 3000}) == 3.0
    assert deadline_seconds({"deadlineMs": 999999}) == DEFAULT_DEADLINE_SECONDS
    assert deadline_seconds(None) == DEFAULT_DEADLINE_SECONDS
    print("app/pricecommon.py: OK")
