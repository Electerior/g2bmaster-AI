"""사양 → 제품 탐색. 설계 2번("제품명이 없고 세부 사양만 나열")의 빠져 있던 단계.

기존 경로는 규격서의 사양 문자열을 **그대로 쇼핑몰 검색어로 썼다**. 실측:

    "GPU memory 96GB GDDR7 With ECC CUDA 24064"  → quotes 1건, GMKtec 미니PC 1,222,080원
    "NVIDIA RTX PRO 6000 Blackwell 96GB"          → quotes 30건, 다나와 23,286,500원

같은 GPU 인데 19배 차이가 나고, 틀린 쪽이 `status=found` 로 성공을 보고한다.
쇼핑몰 검색창은 사양을 못 읽는다 — 사양으로 **모델명을 먼저 알아낸 뒤** 값을 물어야 한다.

여기서는 그 한 단계만 한다: 사양 문자열 → 후보 모델명(+ 화이트리스트 상품 URL).
가격은 만들지 않는다. 값은 언제나 기존 다나와·에누리 파서가 매기고, 그래야
`basis="listed"` 를 지킬 수 있다.

**임의 URL 을 가져오지 않는다.** 검색이 물어온 URL 중 이미 파서가 있는 도메인
(다나와 `pcode`, 에누리 `modelno`)만 골라 넘긴다 — `backend-price-api.md §9-B` 가
B-1(화이트리스트 유지)로 정한 경계를 그대로 지킨다.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from . import wiki as _wiki
from . import hardware_kb as _kb
from . import product_index as _pi
from .config import get_ai_config
from .errors import AiFailure

#: 상품 상세 URL 화이트리스트. 기존 파서가 그대로 소비할 수 있는 형태만 받는다.
_DANAWA_PCODE = re.compile(r"prod\.danawa\.com/info/?\?.*\bpcode=(\d+)", re.I)
_ENURI_MODELNO = re.compile(r"enuri\.com/.*\bmodelno=(\d+)", re.I)

#: 제목에서 모델명을 뽑을 때 버릴 잡음. 쇼핑몰 제목의 관용 접미다.
_TITLE_NOISE = re.compile(
    r"\[[^\]]*\]|\([^)]*\)|정품|병행수입|벌크|중고|리퍼|무료배송|당일발송|최저가|해외구매|쿠팡|공식",
    re.IGNORECASE)

#: 모델명 후보 — 영문·숫자·하이픈이 섞인 덩어리. 순수 단어나 순수 숫자는 모델이 아니다.
_MODEL_CHUNK = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[- ][A-Za-z0-9]+)*")

_TIMEOUT_S = 8.0
_MAX_RESULTS = 20
#: 모델명으로 승격하려면 서로 다른 결과 몇 건이 같은 이름을 지목해야 하는가.
MIN_VOTES = 2


def _searx_base() -> str:
    cfg = get_ai_config()
    return str(cfg.get("searchUrl") or "").rstrip("/")


def _searxng_enabled() -> bool:
    """SearXNG 를 탐색기로 쓰도록 설정돼 있는가."""
    return str(get_ai_config().get("searchProvider") or "").lower() == "searxng" and bool(_searx_base())


def enabled() -> bool:
    """탐색 체인(로컬 KB → Wikipedia → SearXNG)에 켜진 소스가 하나라도 있는가.

    로컬 KB(코퍼스 파일)와 Wikipedia(공개 API)는 설정 없이 켜진다. SearXNG 가
    꺼져 있거나 차단돼도 둘이 GPU·CPU 사양을 잡으므로 탐색 자체는 살아 있다 —
    estimate.py 가 이 값을 보고 탐색 단계를 통째로 건너뛰는 일이 없게 한다.
    """
    return _kb.enabled() or _wiki.enabled() or _searxng_enabled()


async def _search(query: str, deadline_s: float = _TIMEOUT_S) -> tuple[list[dict[str, Any]], list[str]]:
    """(결과, 정지된 엔진 목록). **빈 결과와 차단을 반드시 구분해서 돌려준다.**

    SearXNG 는 상위 엔진이 전부 막혀도 HTTP 200 에 `results: []` 를 준다. 실측에서
    연속 16질의 만에 brave·google cse·startpage·wikipedia 가 모두
    `Suspended: too many requests` 로 정지했고, 30초 뒤에도 풀리지 않았다.
    이걸 "못 찾음"으로 읽으면 차단 구간 내내 조용히 탐색이 죽는다 —
    나라장터 오류 봉투를 "데이터 없음"으로 읽던 것과 같은 함정이다.
    """
    base = _searx_base()
    if not base:
        return [], []
    try:
        async with httpx.AsyncClient(timeout=deadline_s) as client:
            response = await client.get(f"{base}/search",
                                        params={"q": query, "format": "json"})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        # 탐색 실패는 가격 조회 실패가 아니다 — 후보 없이 진행하고 상위가 판단한다.
        raise AiFailure("PRICE_SOURCE_BROKEN", detail=f"searxng: {error}") from error
    results = payload.get("results")
    results = results[:_MAX_RESULTS] if isinstance(results, list) else []
    suspended = [str(e[0]) for e in (payload.get("unresponsive_engines") or [])
                 if isinstance(e, (list, tuple)) and e]
    return results, suspended


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", _TITLE_NOISE.sub(" ", str(title or ""))).strip()


def _model_candidates(title: str) -> list[str]:
    """제목에서 모델명이 될 만한 덩어리를 길이 순으로."""
    chunks = []
    for m in _MODEL_CHUNK.finditer(_clean_title(title)):
        chunk = m.group().strip()
        # 숫자가 없으면 제품군 이름일 뿐 모델이 아니다("NVIDIA", "그래픽카드").
        if len(chunk) >= 4 and any(c.isdigit() for c in chunk):
            chunks.append(chunk)
    chunks.sort(key=len, reverse=True)
    return chunks


def _shop_links(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    """화이트리스트 도메인의 상품 URL 만 추린다."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in results:
        url = str(r.get("url") or "")
        for source, pattern in (("danawa", _DANAWA_PCODE), ("enuri", _ENURI_MODELNO)):
            m = pattern.search(url)
            if not m:
                continue
            key = (source, m.group(1))
            if key in seen:
                continue
            seen.add(key)
            out.append({"source": source, "sourceId": m.group(1), "url": url,
                        "title": _clean_title(r.get("title") or "")})
    return out


async def _searxng_model(spec_text: str, category: str = "",
                         deadline_s: float = _TIMEOUT_S) -> dict[str, Any]:
    """SearXNG 검색 경로. **체인의 마지막 소스** — 직접 부르지 않고 `discover_model` 이 부른다.

    반환은 `discover_model` 과 같은 모양이다. 차단(suspended)과 진짜 못 찾음은
    반드시 구분한다 — 차단을 "없음"으로 읽으면 위키가 놓친 부품이 조용히 unpriced 로
    떨어진다(`searchUnavailable` 플래그가 화면 경고의 근거다).
    """
    query = re.sub(r"\s+", " ", str(spec_text or "")).strip()[:200]
    if not query:
        return {"query": "", "model": None, "candidates": [], "shopLinks": [],
                "status": "not-found", "source": "searxng"}
    if not _searxng_enabled():
        return {"query": query, "model": None, "candidates": [], "shopLinks": [],
                "status": "no-source", "source": "searxng"}

    results, suspended = await _search(f"{query} {category}".strip(), deadline_s)
    if not results:
        # 엔진이 정지해 빈 결과가 온 것과 정말 못 찾은 것은 완전히 다른 사건이다.
        return {"query": query, "model": None, "candidates": [], "shopLinks": [],
                "status": "search-unavailable" if suspended else "not-found",
                "suspendedEngines": suspended, "source": "searxng"}

    # 여러 결과가 함께 지목하는 모델명일수록 신뢰도가 높다 — 제목 간 합의로 고른다.
    votes: dict[str, int] = {}
    candidates: list[dict[str, str]] = []
    for r in results:
        models = _model_candidates(r.get("title") or "")
        top = models[0] if models else None
        if top:
            votes[top] = votes.get(top, 0) + 1
        candidates.append({"title": _clean_title(r.get("title") or ""),
                           "url": str(r.get("url") or ""), "model": top or ""})

    # 득표가 하나뿐이면 모델을 찾은 게 아니라 사양을 되풀이한 것이기 쉽다
    # (실측: "128GB DDR5-6400 …" → "DDR5 6400 64GB" 1표). 합의가 있을 때만 승격한다.
    model = max(votes, key=lambda k: (votes[k], len(k)), default=None)
    if model and votes[model] < MIN_VOTES:
        model = None
    return {
        "query": query,
        "model": model,
        "modelVotes": votes.get(model, 0) if model else 0,
        "candidates": candidates[:8],
        "shopLinks": _shop_links(results),
        "status": "found" if model else "not-found",
        "source": "searxng",
    }


def _enrich(result: dict[str, Any], category: str) -> dict[str, Any]:
    """발견된 모델명을 다나와 상품 인덱스의 정확한 pcode 로 보강한다.

    KB·Wikipedia 는 모델명("RTX 5080")만 알고 다나와 상품 페이지는 모른다. 인덱스가
    있으면 그 모델명으로 상품을 찾아 화이트리스트 링크(danawa pcode)를 shopLinks 에
    얹는다 — estimate 가 검색 없이 pcode 로 등재가를 지목할 수 있게 한다.
    인덱스가 없거나 못 찾아도 치명적이지 않다 — 결과는 그대로 흘러간다.
    """
    model = result.get("model")
    if not model or not _pi.enabled():
        return result
    try:
        links = _pi.lookup(str(model), category)
    except Exception:  # noqa: BLE001 — 인덱스 보강 실패는 발견 실패가 아니다
        return result
    if not links:
        return result
    known = {l.get("sourceId") for l in links}
    merged = links + [l for l in (result.get("shopLinks") or []) if l.get("sourceId") not in known]
    return {**result, "shopLinks": merged}


async def discover_model(spec_text: str, category: str = "",
                         deadline_s: float = _TIMEOUT_S) -> dict[str, Any]:
    """사양 문자열로 제품을 탐색한다 — **소스 우선순위 체인**.

    체인: 로컬 KB(1차, 차단 불가·오프라인) → Wikipedia(2차, 공개 API) →
    SearXNG(3차, 긴꼬리 보조). 한 소스가 차단돼도 다음 소스가 이어받는다 —
    SearXNG 의 suspension 은 "실패"가 아니라 "이번엔 건너뜀"일 뿐이다.

    반환은 **후보뿐**이다 — 가격도, 확정 모델도 만들지 않는다.
    선택은 상위(그리고 최종적으로 백엔드)의 몫이다.

        {"query": ..., "model": "RTX PRO 6000 Blackwell" | None,
         "candidates": [{"title", "url", "model"}],
         "shopLinks": [{"source", "sourceId", "url", "title"}],
         "status": "found" | "not-found" | "no-source" | "search-unavailable",
         "source": "hardware-kb" | "wikipedia" | "searxng"}
    """
    query = re.sub(r"\s+", " ", str(spec_text or "")).strip()[:200]
    if not query:
        return {"query": "", "model": None, "candidates": [], "shopLinks": [],
                "status": "not-found", "source": "chain"}
    if not enabled():
        return {"query": query, "model": None, "candidates": [], "shopLinks": [],
                "status": "no-source", "source": "chain"}

    # 1) 로컬 하드웨어 KB(RAG) — 네트워크 없음. GPU·CPU 는 대부분 여기서 끝난다.
    if _kb.enabled():
        try:
            found = await _kb.identify(query, category, deadline_s)
        except AiFailure:
            found = None
        if found and found.get("status") == "found":
            return _enrich(found, category)

    # 2) Wikipedia — 공개 API. KB 를 못 만든 신모델·부족한 사양표를 채운다.
    if _wiki.enabled():
        try:
            found = await _wiki.discover_model(query, category, deadline_s)
        except AiFailure:
            found = None
        if found and found.get("status") == "found":
            return _enrich(found, category)

    # 3) SearXNG — 일반 웹. 위 둘이 없는 부품(장수 브랜드·쇼핑몰 링크)을 노린다.
    if _searxng_enabled():
        try:
            found = await _searxng_model(query, category, deadline_s)
        except AiFailure:
            found = {"status": "not-found", "suspendedEngines": [], "source": "searxng"}
        if found.get("status") in ("found", "search-unavailable"):
            return found

    return {"query": query, "model": None, "candidates": [], "shopLinks": [],
            "status": "not-found", "source": "chain"}
