"""규격서 → 부품 단가 추정. **ITMAYA 색인(원본) + 다나와 웹검색 하이브리드.**

원본 `price-estimator.js` 를 이식하고 웹검색을 폴백으로 붙였다.

  1) **ITMAYA GPU서버 가격표 색인** (`data/ITMAYA_GPU서버_가격표.xlsx` '전체옵션' 시트)
     — GPU 서버 규격서면 System·Processor·GPU·Memory·Storage 슬롯을 규칙 매칭해 합산한다.
     완제품 베이스(System)를 인식하고, GPU 장수를 파싱해 곱한다. 원본과 같은 로직이다.
  2) **다나와 웹검색 폴백** (`app.price`) — 색인이 못 잡는 것(비-GPU서버, 색인에 없는 부품)은
     규격서에서 LLM 으로 부품을 뽑아 다나와에서 실시간 조회한다.

원본이 LLM 을 안 쓴 이유는 ITMAYA 카탈로그가 정형이라 규칙으로 충분했기 때문이다. 그 정확도를
살리되, 카탈로그 밖 품목은 웹으로 넓힌다 — 두 접근의 장점을 합친다.

응답 규격은 프론트 계약 `EstimatedUnitCost`(union). `breakdown[].source` 로 색인/웹을 구분한다.
"""

from __future__ import annotations

import asyncio
import json
import re

from . import discover
from .errors import AiFailure
from .itmaya import load_index as _load_index, _num  # noqa: F401 (_num: 색인 로더와 함께 이전)
from .llm.client import lms_chat, loaded_model
from .part_resolver import derive_product_identity, quote_matches_identity
from .prebuilt import classify_prebuilt_bundle, find_prebuilt_comparables
from .price import resolve as resolve_price

# ── ITMAYA 색인 상수 (원본 price-estimator.js) ───────────────────────────────
# _load_index()/_num() 은 app/itmaya.py 로 옮겼다(멀티소스 리졸버와 공유). 여기선 import 만 한다.

#: System 제품별 GPU 슬롯 수 — 베이스 폴백에서 GPU 장수를 수용하는 가장 싼 System 을 고를 때.
SYSTEM_SLOTS = {
    "ESC4000-E11": 4, "531A-I": 2, "741GE-TNRT": 4, "ESC4000A-E12": 4,
    "ESC8000-E12": 8, "ESC8000A-E13": 8, "421GE-TNRT": 10, "821GE-TNHR": 8,
}
CORE = ["System", "Processor", "GPU", "Memory", "Storage"]
OPTIONAL = ["RAID", "NIC", "Support (OS 설치)"]

# GPU 서버 신호가 없으면 ITMAYA 카탈로그 대상이 아니다(노트북·사무기기·일반 워크스테이션 오탐 차단).
#
# **바뀐 점(2026-08):** 예전엔 "워크스테이션/workstation" 단독으로 이 카탈로그가 켜져서, 실제 사고가
# 났다 — "암호 알고리즘 구현용 워크스테이션·노트북·전력 파형 측정용 보드" 같은 일반 물품이 GPU
# 서버로 오인돼 ESC/System 섀시가 GPU·Storage 칸에 엉뚱하게 채워졌다. ITMAYA GPU 서버는 정의상
# **가속 GPU 를 단다** — 그래서 실제 가속기 모델(H100/H200/A100/L40/RTX 40·50/RTX PRO…)이나
# 명시적 "GPU 서버/가속 서버/GPU 워크스테이션" 문구가 있을 때만 켠다. 맨 워크스테이션은 제외.
_GPU_SERVER = re.compile(
    r"tesla|\bh100\b|\bh200\b|\ba100\b|\ba30\b|\ba40\b|\ba10\b|\bl40s?\b|\bl4\b|"
    r"rtx\s?40\d0|rtx\s?50\d0|rtx\s?pro\s?\d{3,4}|quadro|gpgpu|gpu\s*서버|가속\s*서버|딥\s*러닝|"
    r"deep\s*learning|추론\s*서버|ai\s*(?:학습|추론|서버)|gpu\s*(?:워크스테이션|workstation)",
    re.IGNORECASE,
)
_NON_SERVER = re.compile(
    r"노트북|랩탑|laptop|태블릿|tablet|일체형|올인원|all-?in-?one|모니터|프린터|복합기|스캐너",
    re.IGNORECASE,
)
_SERVER_CTX = re.compile(r"서버|server|랙마운트|랙\s*마운트|rack\s*mount|데이터\s*센터|워크스테이션", re.IGNORECASE)

_TOKEN_SPLIT = re.compile(r"[^a-z0-9가-힣]+")


def _tokens(s: str) -> list[str]:
    text = str(s or "").lower()
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z])(\d)", r"\1 \2", text)
    return [t for t in _TOKEN_SPLIT.split(text) if len(t) >= 2]


def _score_name(qset: set[str], name: str) -> int:
    score = 0
    for tk in set(_tokens(name)):
        if tk in qset:
            score += 2 if tk[0].isdigit() else 1   # 숫자 토큰(용량·모델) 가중
    return score


def _best_in_category(index: dict, category: str, qset: set[str], min_score: int) -> dict | None:
    best, best_score = None, 0
    for opt in index["byCategory"].get(category, []):
        sc = _score_name(qset, opt["name"])
        if sc > best_score:
            best_score, best = sc, opt
    return {**best, "score": best_score} if best and best_score >= min_score else None


def _parse_gpu_count(text: str) -> int | None:
    s = str(text or "")
    for pat in (r"(\d{1,2})\s*(?:gpu|장|way|-way|ea|개|slot)",
                r"gpu\s*[:x]?\s*(\d{1,2})",
                r"(\d{1,2})\s*[×x]\s*(?:gpu|rtx|nvidia)"):
        m = re.search(pat, s, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 16:
                return n
    return None


def _fallback_base(index: dict, gpu_count: int) -> dict | None:
    if not gpu_count:
        return None
    systems = [s for s in index["byCategory"].get("System", []) if SYSTEM_SLOTS.get(s["product"], 0) >= gpu_count]
    systems.sort(key=lambda s: s["low"])
    return {**systems[0], "score": 0, "inferred": True} if systems else None


def _is_gpu_server_text(text: str) -> bool:
    if _NON_SERVER.search(text) and not _SERVER_CTX.search(text):
        return False
    return bool(_GPU_SERVER.search(text))


def _estimate_from_itmaya(text: str) -> dict:
    """원본 estimateUnitCost 이식 — ITMAYA 색인으로 GPU 서버 단가를 합산한다."""
    index = _load_index()
    if not index:
        return {"matched": False, "reason": "price-table-unavailable"}
    if not _is_gpu_server_text(text):
        return {"matched": False, "reason": "not-gpu-server"}
    qset = set(_tokens(text))
    if not qset:
        return {"matched": False, "reason": "empty-input"}

    parsed_gpu = _parse_gpu_count(text)
    gpu_count = parsed_gpu or 1
    breakdown, low, high, has_base = [], 0, 0, False

    for cat in CORE:
        m = _best_in_category(index, cat, qset, 1 if cat == "System" else 2)
        if not m and cat == "System" and parsed_gpu:
            m = _fallback_base(index, parsed_gpu)
        if not m:
            continue
        qty = gpu_count if cat == "GPU" else 1
        low += m["low"] * qty
        high += m["high"] * qty
        if cat == "System":
            has_base = True
        breakdown.append({"category": cat, "option": m["name"], "product": m["product"],
                          "qty": qty, "low": m["low"], "high": m["high"],
                          "inferred": bool(m.get("inferred")),
                          "role": "base" if cat == "System" else "part", "source": "itmaya"})

    if not any(b["category"] in ("System", "GPU") for b in breakdown):
        return {"matched": False, "reason": "not-gpu-server", "gpuCount": parsed_gpu}

    for cat in OPTIONAL:
        m = _best_in_category(index, cat, qset, 3)   # 옵션은 확실히 언급된 것만
        if not m:
            continue
        low += m["low"]
        high += m["high"]
        breakdown.append({"category": cat, "option": m["name"], "product": m["product"],
                          "qty": 1, "low": m["low"], "high": m["high"], "inferred": False,
                          "role": "part", "source": "itmaya"})

    if not breakdown:
        return {"matched": False, "reason": "no-match", "gpuCount": gpu_count}
    return {"matched": True, "low": low, "high": high, "mid": round((low + high) / 2),
            "gpuCount": gpu_count, "hasBase": has_base, "breakdown": breakdown, "currency": "KRW"}


# ── 웹 폴백 (LLM 부품추출 + 다나와) ──────────────────────────────────────────
PART_CATEGORIES = ("CPU", "GPU", "RAM", "SSD", "HDD", "메인보드", "파워", "케이스", "쿨러", "네트워크")
_NOISE = re.compile(r"중고|리퍼|refurb|벌크|bulk|병행수입|해외구매", re.IGNORECASE)
_CATEGORY_HINT = {"RAM": "메모리", "SSD": "SSD", "HDD": "하드디스크", "메인보드": "메인보드", "파워": "파워서플라이"}

PART_PROMPT = """너는 조달 규격서에서 하드웨어 부품 구성을 뽑는 분석가다.
구매 대상 부품만 골라 목록으로 만든다.
- category: CPU, GPU, RAM, SSD, HDD, 메인보드, 파워, 케이스, 쿨러, 네트워크 중 하나
- name: 다나와에서 검색할 구체 모델명(예: "NVIDIA H200 141GB"). 모델이 없으면 사양 그대로.
- qty: 수량(정수), 없으면 1
- named: 규격서에 제품명·모델명이 **적혀 있으면** true, 사양만 있어 네가 추론했으면 false
- evidence: 이 부품의 근거가 된 **규격서 원문 한 줄을 그대로 복사**한다.
  요약하거나 바꿔 쓰지 마라. 원문에 없는 문장을 쓰면 그 부품은 버려진다.
소프트웨어·용역·설치·보증은 부품이 아니다.
JSON 배열 하나로만 답한다. 없으면 [].
[{"category":"GPU","name":"NVIDIA H200 141GB","qty":3,"named":true,
  "evidence":"GPU: NVIDIA H200 141GB 3장"}]"""


def _parse_array(text: str) -> list[dict]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except ValueError:
        return []
    return [x for x in parsed if isinstance(x, dict)] if isinstance(parsed, list) else []


async def _extract_parts(spec_text: str) -> list[dict]:
    model = await loaded_model()
    response = await lms_chat({
        "model": model,
        "messages": [{"role": "system", "content": PART_PROMPT}, {"role": "user", "content": spec_text[:12000]}],
        "temperature": 0, "max_tokens": 1500,
    })
    parts = []
    for e in _parse_array(response["choices"][0]["message"]["content"]):
        name = str(e.get("name") or "").strip()
        cat = str(e.get("category") or "").strip()
        if not name or cat not in PART_CATEGORIES:
            continue
        try:
            qty = max(1, int(e.get("qty") or 1))
        except (TypeError, ValueError):
            qty = 1
        # `named`·`evidence` 는 우리가 판정에 쓰지 않는다 — 백엔드가 규격서 원문과 대조할
        # 재료다(경계 계약 §4: AI 가 자기 응답을 자기가 검증하면 검증이 아니다).
        evidence = str(e.get("evidence") or "").strip()[:300]
        parts.append({"category": cat, "name": name, "qty": qty,
                      "named": bool(e.get("named")), "evidence": evidence})
    return parts


async def _resolve_spec_only(part: dict) -> dict:
    """사양만 있는 부품(설계 2번)을 탐색으로 모델명까지 끌어올린다.

    쇼핑몰 검색창은 사양을 못 읽는다. 탐색이 모델을 찾아내면 그 이름으로 값을 묻고,
    못 찾으면 원래 이름 그대로 둔다 — 이 단계는 <b>이름을 바꿀 뿐 가격을 만들지 않는다</b>.
    """
    # 판단 기준은 `named` 하나다. `derive_product_identity(...)["strong"]` 을 쓰면 안 된다 —
    # 사양 문자열에서 `DDR5`·`SP5`·`2U` 를 모델명으로 오인해(실측) 탐색이 통째로 건너뛰어진다.
    # 규격서가 제품명을 적었는지는 규격서를 읽은 쪽만 안다.
    if part.get("named") or not discover.enabled():
        return part
    try:
        found = await discover.discover_model(part["name"], part["category"])
    except AiFailure:
        return part          # 탐색 실패는 가격 실패가 아니다
    if found.get("status") == "search-unavailable":
        # 탐색기가 막혔다. 이름을 못 바꾼 채 진행하되 그 사실을 남긴다 — 남기지 않으면
        # "규격서에 없는 부품"과 "탐색이 죽어서 못 찾은 부품"이 화면에서 구분되지 않는다.
        return {**part, "searchUnavailable": True,
                "suspendedEngines": found.get("suspendedEngines", [])}
    if found.get("status") != "found" or not found.get("model"):
        return part
    return {**part, "name": found["model"], "discoveredFrom": part["name"],
            "discovery": {"model": found["model"], "votes": found.get("modelVotes", 0),
                          "shopLinks": found.get("shopLinks", [])[:3]}}


async def _price_part(part: dict) -> dict:
    hint = _CATEGORY_HINT.get(part["category"], "")
    query = f"{part['name']} {hint}".strip() if hint and hint not in part["name"] else part["name"]
    result = await resolve_price({"itemName": query, "deadlineMs": 8000})

    # 사양 대조(part_resolver) — 완제품 PC·중고·렌탈·액세서리, 그리고 요청과 다른 용량을 버린다.
    # 이게 없으면 "DDR5 512GB" 검색에 데스크탑 완제품이 최저가로 앉는다(원본이 고친 문제).
    identity = derive_product_identity(part["name"])
    verified = []
    for q in result.get("quotes") or []:
        if not isinstance(q.get("priceKrw"), int) or q["priceKrw"] <= 0:
            continue
        verdict = quote_matches_identity({"title": q.get("name"), "price": q["priceKrw"], "url": q.get("url")}, identity)
        if verdict["ok"]:
            verified.append(q)

    # 모델을 특정 못 하는 순수 사양(strong=False)은 대조가 느슨하다 — 그땐 노이즈(중고 등)만 걸러
    # 참고값으로 쓰되 inferred 로 표시한다. 모델이 뚜렷하면(strong) 검증 통과분만 신뢰한다.
    if not verified and not identity["strong"]:
        verified = [q for q in (result.get("quotes") or [])
                    if isinstance(q.get("priceKrw"), int) and q["priceKrw"] > 0 and not _NOISE.search(q.get("name") or "")]

    if not verified:
        # 가격을 못 찾은 행은 어떤 소스가 이겼는지 알 수 없다 — source=None(가격도 None).
        return {"category": part["category"], "option": part["name"], "product": None,
                "qty": part["qty"], "low": None, "high": None, "inferred": True,
                "role": "part", "source": None}
    prices = sorted(q["priceKrw"] for q in verified)
    cheapest = min(verified, key=lambda q: q["priceKrw"])
    # 최저가를 낸 소스를 그대로 실어 _merge_estimates 의 priceSource 가 정확하게 나오게 한다
    # (멀티소스 리졸버는 danawa·enuri·itmaya 어디서든 후보를 줄 수 있다).
    return {"category": part["category"], "option": part["name"], "product": cheapest["name"],
            "qty": part["qty"], "low": prices[0], "high": prices[-1],
            "inferred": not identity["strong"], "role": "part",
            "source": cheapest.get("source", "danawa"),
            # 백엔드가 규격서 원문과 대조할 재료. 우리는 판정하지 않는다.
            "named": bool(part.get("named")), "evidence": part.get("evidence", ""),
            "discoveredFrom": part.get("discoveredFrom"), "discovery": part.get("discovery"),
            "searchUnavailable": bool(part.get("searchUnavailable"))}


async def _estimate_from_web(spec_text: str, item_name: str = "") -> dict:
    parts = await _extract_parts(spec_text)
    if not parts:
        return {"matched": False, "reason": "규격서에서 가격을 매길 하드웨어 부품을 찾지 못했습니다."}

    # 설계 2번 — 사양만 적힌 부품은 값을 묻기 전에 모델명부터 알아낸다.
    # 이 단계가 없으면 사양 문자열이 그대로 쇼핑몰 검색어가 되어 엉뚱한 물건이 최저가로 앉는다.
    parts = list(await asyncio.gather(*(_resolve_spec_only(p) for p in parts)))

    # 완제품 판정 — 이 부품 구성이 "완본체를 사는 것"이면 부품 합보다 완제품 최저가가 맞다.
    # 부품 가격 조회와 병렬로 돌린다(둘 다 다나와를 치지만 독립적이다).
    group_name = item_name or (spec_text.strip().split("\n", 1)[0][:80] if spec_text else "")
    prebuilt_task = find_prebuilt_comparables(
        {"name": group_name, "components": [{"name": p["name"]} for p in parts]})

    breakdown, prebuilt = await asyncio.gather(
        asyncio.gather(*(_price_part(p) for p in parts)),
        prebuilt_task,
    )
    priced = [b for b in breakdown if b["low"] is not None]
    if not priced:
        return {"matched": False, "reason": "부품을 식별했지만 다나와에서 가격을 찾지 못했습니다.",
                "gpuCount": sum(p["qty"] for p in parts if p["category"] == "GPU")}
    low = sum(b["low"] * b["qty"] for b in priced)
    high = sum(b["high"] * b["qty"] for b in priced)
    return {
        "matched": True, "low": low, "high": high, "mid": (low + high) // 2,
        "gpuCount": sum(b["qty"] for b in breakdown if b["category"] == "GPU"),
        "hasBase": len(priced) == len(breakdown), "breakdown": list(breakdown), "currency": "KRW",
        # 완제품일 수 있으면 부품 합과 함께 완제품 최저가 후보를 실어 준다 — 백엔드/화면이 비교한다.
        "prebuilt": {
            "isPrebuilt": prebuilt.get("isPrebuilt", False),
            "score": prebuilt.get("score", 0),
            "reason": prebuilt.get("reason", ""),
            "comparables": prebuilt.get("comparables", []),
        },
    }


# ── 병합 (베어본 + 부품) ─────────────────────────────────────────────────────
# 카테고리 이름이 색인(Processor/Memory/Storage)과 웹(CPU/RAM/SSD/HDD)에서 다르다.
# 같은 부품을 두 번 세지 않으려면 하나의 축으로 모아 비교해야 한다.
_CANON_CAT = {
    "processor": "cpu", "cpu": "cpu",
    "gpu": "gpu", "vga": "gpu", "그래픽카드": "gpu",
    "memory": "ram", "ram": "ram", "메모리": "ram",
    "storage": "storage", "ssd": "storage", "hdd": "storage", "저장장치": "storage",
    "system": "base",
}


def _canon_cat(cat: str) -> str:
    return _CANON_CAT.get(str(cat or "").strip().lower(), str(cat or "").strip().lower())


def _dedup_key(row: dict) -> tuple[str, str]:
    """(정규 카테고리, 모델) — 같은 부품을 색인·웹 양쪽에서 중복 계상하지 않기 위한 키."""
    cat = _canon_cat(row.get("category"))
    name = str(row.get("option") or row.get("product") or "")
    ident = derive_product_identity(name)
    model = (ident.get("model") or "").lower() or re.sub(r"[^a-z0-9가-힣]+", "", name.lower())
    return cat, model


def _sum_priced(rows: list[dict]) -> tuple[int, int]:
    low = sum(r["low"] * r["qty"] for r in rows if r.get("low") is not None)
    high = sum((r["high"] if r.get("high") is not None else r["low"]) * r["qty"]
               for r in rows if r.get("low") is not None)
    return low, high


def _merge_estimates(itmaya: dict, web: dict) -> dict:
    """베어본(색인)과 부품(웹)을 하나의 breakdown 으로 합친다.

    규칙: 부품을 종합적으로 수집(웹 LLM)하고, 그 부품을 담는 베어본(색인 System)이 잡히면
    베어본을 함께 나열한다. 같은 부품이 양쪽에 있으면 색인(정형·결정적)을 신뢰해 하나만 남긴다.
    """
    itmaya_rows = list(itmaya.get("breakdown") or []) if itmaya.get("matched") else []
    web_rows = list(web.get("breakdown") or []) if web.get("matched") else []
    if not itmaya_rows and not web_rows:
        # 둘 다 실패 — 더 구체적인 사유(웹)를 우선하되 gpuCount 는 살린다.
        reason = web.get("reason") or itmaya.get("reason") or "부품을 식별하지 못했습니다."
        out = {"matched": False, "reason": reason}
        if itmaya.get("gpuCount") or web.get("gpuCount"):
            out["gpuCount"] = itmaya.get("gpuCount") or web.get("gpuCount")
        return out

    # 색인 우선(베어본·정형 부품) → 웹으로 색인이 못 담은 부품을 채운다.
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in itmaya_rows + web_rows:
        key = _dedup_key(row)
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)

    # 베어본이 맨 위로 오게 정렬(base → part). 나머지 순서는 유지.
    merged.sort(key=lambda r: 0 if r.get("role") == "base" else 1)

    low, high = _sum_priced(merged)
    has_base = any(r.get("role") == "base" for r in merged)
    gpu_count = sum(r["qty"] for r in merged if _canon_cat(r.get("category")) == "gpu")
    # 가격을 낸 행의 소스만 센다 — 가격 없는 행(source=None)은 priceSource 를 흐리면 안 된다.
    sources = {r.get("source") for r in merged if r.get("source")}
    price_source = "hybrid" if len(sources) > 1 else (next(iter(sources), None) or "danawa")

    out = {
        "matched": True, "low": low, "high": high, "mid": (low + high) // 2,
        "gpuCount": gpu_count, "hasBase": has_base, "breakdown": merged,
        "allPriced": all(r.get("low") is not None for r in merged),
        "currency": "KRW", "priceSource": price_source,
    }
    if isinstance(web.get("prebuilt"), dict):
        out["prebuilt"] = web["prebuilt"]   # 완제품 판정·후보는 웹 경로가 만든다
    return out


async def estimate_unit_cost(payload: dict) -> dict:
    """규격서 텍스트 → 원가 추정.

    **부품을 항상 종합 수집(웹 LLM+다나와)하고, 담는 베어본(ITMAYA 색인)이 잡히면 함께 나열한다.**
    예전엔 색인이 맞으면 즉시 반환해 베어본만 나오고 부품이 누락됐다 — 그 either/or 를 없앴다.
    """
    spec_text = str(payload.get("specText") or "").strip()
    if not spec_text:
        return {"matched": False, "reason": "규격서 텍스트가 없습니다."}

    # 베어본 색인(동기·결정적)과 부품 수집(웹)을 함께 돌려 합친다.
    itmaya = _estimate_from_itmaya(spec_text)
    web = await _estimate_from_web(spec_text)
    return _merge_estimates(itmaya, web)


if __name__ == "__main__":
    idx = _load_index()
    assert idx and "System" in idx["byCategory"], "ITMAYA 색인 로드 실패"
    # GPU 서버 규격서 → ITMAYA 경로
    r = _estimate_from_itmaya("GPU 서버, NVIDIA H200 8장, ESC8000-E12, DDR5 512GB")
    print("ITMAYA:", r.get("matched"), r.get("mid"), "gpuCount", r.get("gpuCount"))
    assert _parse_gpu_count("H200 8장") == 8
    assert not _is_gpu_server_text("사무용 노트북 30대")
    # 회귀(2026-08): 맨 '워크스테이션' 만으론 GPU 서버 아님 — 암호 워크스테이션·노트북·보드 오탐 차단
    assert not _is_gpu_server_text("암호 알고리즘 구현용 워크스테이션, 노트북 및 전력 파형 측정용 보드 구매")
    assert not _is_gpu_server_text("워크스테이션 2대, 노트북 3대")
    # 진짜 가속기가 있으면 여전히 켜진다
    assert _is_gpu_server_text("GPU 워크스테이션 H100 4장")
    assert _is_gpu_server_text("딥러닝 서버 RTX PRO 6000 x8")
    # 오탐이던 물품은 ITMAYA 경로를 아예 타지 않는다(웹 부품추출로 감)
    assert not _estimate_from_itmaya("암호 알고리즘 워크스테이션, 노트북, 전력 파형 측정 보드").get("matched")

    # ── 병합: 베어본(색인) + 부품(웹) 함께 나열, 중복 없음 ──────────────────────
    itmaya = {"matched": True, "breakdown": [
        {"category": "System", "option": "ESC8000-E12", "product": "ESC8000-E12", "qty": 1,
         "low": 10_000_000, "high": 10_000_000, "role": "base", "source": "itmaya"},
        {"category": "GPU", "option": "NVIDIA H200 141GB", "product": "H200", "qty": 8,
         "low": 5_000_000, "high": 5_000_000, "role": "part", "source": "itmaya"},
    ]}
    web = {"matched": True, "breakdown": [
        {"category": "GPU", "option": "NVIDIA H200 141GB", "product": "다나와 H200", "qty": 8,
         "low": 4_900_000, "high": 5_100_000, "role": "part", "source": "danawa"},   # 색인과 중복
        {"category": "SSD", "option": "삼성 990 PRO 4TB", "product": "990 PRO", "qty": 2,
         "low": 500_000, "high": 600_000, "role": "part", "source": "danawa"},        # 색인이 못 담은 부품
    ], "prebuilt": {"isPrebuilt": False}}
    m = _merge_estimates(itmaya, web)
    assert m["matched"] and m["hasBase"], m
    assert m["breakdown"][0]["role"] == "base", "베어본이 맨 위"
    assert len([x for x in m["breakdown"] if _canon_cat(x["category"]) == "gpu"]) == 1, "GPU 중복 제거"
    assert any(_canon_cat(x["category"]) == "storage" for x in m["breakdown"]), "웹 부품(SSD) 합류"
    assert m["gpuCount"] == 8 and m["priceSource"] == "hybrid", m
    assert m["low"] == 10_000_000 + 5_000_000 * 8 + 500_000 * 2, m["low"]
    assert "prebuilt" in m
    # 베어본 없이 웹 부품만 있을 때도 동작
    only_web = _merge_estimates({"matched": False, "reason": "not-gpu-server"}, web)
    assert only_web["matched"] and not only_web["hasBase"], only_web
    # 둘 다 실패
    both_fail = _merge_estimates({"matched": False, "reason": "not-gpu-server"},
                                 {"matched": False, "reason": "부품 없음"})
    assert not both_fail["matched"] and both_fail["reason"] == "부품 없음", both_fail
    print("app/estimate.py: OK")
