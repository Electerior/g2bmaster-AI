"""규격서 → 부품 단가 추정. 웹 수집 단일 경로(LLM 부품 추출 → 탐색 → 다나와·에누리).

ITMAYA GPU서버 가격표 색인 경로는 제거됐다(2026-08-14) — stale 옵션 행이 규격서와
다른 용량(64GB vs 128GB)으로 dedup 키를 선점해 웹의 정확한 견적을 잘라먹는 문제 때문.
카탈로그 밖 품목은 물론 GPU 서버 규격서도 전부 웹 수집으로 넓힌다.

응답 규격은 프론트 계약 `EstimatedUnitCost`(union). `breakdown[].source` 로 소스를 구분한다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re

from . import discover
from . import product_index
from .errors import AiFailure
from .llm.client import lms_chat, loaded_model
from .part_resolver import derive_product_identity, quote_matches_identity
from .prebuilt import classify_prebuilt_bundle, find_prebuilt_comparables
from .price import resolve as resolve_price
from .price import quotes_by_pcode

_TOKEN_SPLIT = re.compile(r"[^a-z0-9가-힣]+")


def _tokens(s: str) -> list[str]:
    text = str(s or "").lower()
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
    text = re.sub(r"([a-z])(\d)", r"\1 \2", text)
    return [t for t in _TOKEN_SPLIT.split(text) if len(t) >= 2]


# ── 웹 수집 (LLM 부품추출 + 다나와·에누리) ───────────────────────────────────
PART_CATEGORIES = ("CPU", "GPU", "RAM", "SSD", "HDD", "메인보드", "파워", "케이스", "쿨러", "네트워크")
_NOISE = re.compile(r"중고|리퍼|refurb|벌크|bulk|병행수입|해외구매|해외배송|해외직구", re.IGNORECASE)
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


def _quote_llm_enabled() -> bool:
    """후보 선택 LLM 심사를 켤지. `QUOTE_LLM=0` 이면 항상 꺼짐."""
    return os.getenv("QUOTE_LLM", "1") != "0"


def _quote_llm_min_spread() -> float:
    """강한 신원에서도 최고/최저 가격비가 이 값 이상이면 LLM 심사를 부른다.
    약한 신원(모델 없는 사양)은 비율과 무관하게 항상 심사한다."""
    try:
        value = float(os.getenv("QUOTE_LLM_MIN_SPREAD", "1.5"))
    except ValueError:
        return 1.5
    return max(1.0, value)


async def _llm_pick_quotes(part: dict, quotes: list[dict], max_candidates: int = 20) -> list[dict] | None:
    """후보 몇십 개 중 요청 부품과 같은 물건을 LLM 이 한 번에 고른다.

    규칙(part_resolver)이 1차로 걸러낸 후보를 번호 붙여 나열하고, LLM 은 "같은
    물건인 번호들"을 배열로 답한다. 목록 밖 번호·파싱 실패·빈 배열은 **None** —
    호출부는 규칙 결과를 그대로 쓴다(LLM 은 중재자일 뿐 가격을 만들지 않는다).

    실패는 조용하다 — LLM 이 죽었어도 원가 추정은 규칙만으로 계속돈다.
    """
    if not _quote_llm_enabled() or len(quotes) < 2:
        return None
    try:
        from .llm.client import loaded_model, lms_chat

        model = await loaded_model()
        if not model:
            return None
    except (ImportError, AiFailure):
        return None
    candidates = quotes[:max_candidates]
    lines = []
    for i, q in enumerate(candidates, start=1):
        price = f"{q.get('priceKrw'):,}" if isinstance(q.get("priceKrw"), int) else "?"
        lines.append(f"{i}. [{q.get('source')}] {q.get('name')} — {price}원")
    evidence = str(part.get("evidence") or "")[:400]
    prompt = (
        f"요청 부품: {part.get('name')} (분류: {part.get('category')})\n"
        f"규격서 근거: {evidence or '(없음)'}\n\n"
        "후보 견적:\n" + "\n".join(lines) + "\n\n"
        "위 후보 중 요청 부품의 요구(분류·용량·규격)를 **충족하는** 견적의 번호를 모두 배열로 답하라. "
        "다른 종류의 물건(다른 부품·완제품·묶음·다른 용량)은 빼라. "
        "요청이 모델을 특정하지 않으면 요구를 충족하는 다른 모델도 포함한다. "
        "하나도 충족하지 못하면 빈 배열. 배열 외에는 아무 말도 하지 말라."
    )
    try:
        response = await lms_chat({
            "model": model,
            "messages": [
                {"role": "system", "content":
                 "너는 하드웨어 견적 심사관이다. 주어진 후보 중 요청 부품의 요구를 충족하는 견적의 "
                 "번호만 JSON 배열로 답한다. 판단이 서지 않으면 충족하지 못하는 것으로 보고 뺀다."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0, "max_tokens": 300,
        })
        raw = str(response["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError, AiFailure):
        return None
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        picked = [int(x) for x in json.loads(raw[start:end + 1])]
    except (ValueError, TypeError):
        return None
    if os.getenv("QUOTE_DEBUG") == "1":
        print(f"[quote-llm] {part.get('name','')[:40]} → {picked}", flush=True)
    # [] 도 판정이다 — "같은 물건이 없다"와 "판정 실패(None)"를 반드시 구분한다.
    # 빈 판정을 None 으로 흘리면 완화 경로가 잡음을 다시 채운다(실측 사고).
    return [candidates[i - 1] for i in picked if 1 <= i <= len(candidates)]


async def _llm_price_estimate(part: dict) -> dict | None:
    """가격을 못 찾은 부품의 시장가를 LLM 이 추정한다 — 가격 없는 행을 남기지 않는다.

    쇼핑몰 검색(다나와·에누리)이 결정적으로 실패했을 때 마지막 폴백. 추정이므로
    항상 inferred(참고값)로 돌아가고, 확정 원가 합산(백엔드)에는 섞이지 않는다.
    추정이 엉망이면(비정상 범위·파싱 실패) None — 그때만 unpriced 로 남는다.
    """
    if not _quote_llm_enabled():
        return None
    prompt = (f"분류: {part['category']}\n"
              f"부품: {part['name']}\n"
              f"규격서 근거: {part.get('evidence') or '(없음)'}")
    try:
        model = await loaded_model()
        response = await lms_chat({
            "model": model,
            "messages": [
                {"role": "system", "content":
                 "너는 국내 하드웨어 유통 시세에 밝은 조달 견적사다. 쇼핑몰 검색으로 가격을 "
                 "찾지 못한 부품의 단품 시세를 추정해라. 신품·정품 기준(중고·벌크·병행수입 제외), "
                 "국내 등록가(다나와·에누리) 수준이다. "
                 '{"similar": 시세 기준으로 삼은 대표 유통 제품 하나의 정확한 모델명, "low": 최저가, "high": 최고가} — '
                 "원 단위 정수. similar 는 여러 개 나열하지 말고 대표 제품 하나만. "
                 "요구의 코어 수·스레드 수·용량을 충족하는 제품을 similar 로 지목해라(부족한 제품 금지). "
                 "요구가 서버용(Xeon·EPYC·스레드리퍼)이 아니면 데스크톱용 제품을 similar 로 지목해라. "
                 "JSON 객체 하나로만 답해라. 추정이 불가능하면 low 와 high 를 0으로 답해라."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0, "max_tokens": 300,
        })
        raw = str(response["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError, AiFailure):
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        guess = json.loads(raw[start:end + 1])
        low, high = int(guess["low"]), int(guess["high"])
        similar = str(guess.get("similar") or "").strip()[:120]
    except (ValueError, TypeError, KeyError):
        return None
    if low < 1000 or high < low or high > 1_000_000_000 or high > low * 100:
        return None
    if os.getenv("QUOTE_DEBUG") == "1":
        print(f"[quote-llm] 추정 {part.get('name','')[:40]} → {similar[:40]} {low:,}~{high:,}", flush=True)
    return {"low": low, "high": high, "similar": similar or "시장가 추정(LLM)"}


# 대체(유사 제품) 허용 범위 — "요구와 다른 모델"·"다른 용량"은 가장 비슷한 제품으로 채운다.
# 완제품·중고·묶음(개수×용량)·잘못된 견적은 대체 대상에서도 뺀다.
_SUB_BAD_REASON = re.compile(r"excluded-kind|묶음|invalid-|missing-title")


def _substitute_note(reason: str) -> str:
    if reason.startswith("identity-token-missing"):
        return "요구 모델과 다른 제품 중 가장 비슷한 제품"
    if reason.startswith("spec-mismatch"):
        return "요구 용량과 다른 제품 중 가장 비슷한 제품"
    return "가장 비슷한 제품으로 대체"


def _token_overlap(query: str, name: str) -> int:
    qset = set(_tokens(query))
    if not qset:
        return 0
    score = 0
    for tk in set(_tokens(name)):
        if tk in qset:
            score += 2 if tk[0].isdigit() else 1
    return score


# 서버/워크스테이션 CPU — 데스크톱 요구에 이걸 붙이면 안 된다.
# 실측: "AMD AM5 12코어 24스레드 4.4GHz"(라이젠 9 급 요구)에 EPYC 4584PX(서버용 AM5)가
# 유사 상품으로 제안됐다 — 같은 소켓이라도 등급이 다르고, 함께 매칭된 소비자 X870E
# 보드에 장착조차 불가능하다. 요구와 후보의 서버/데스크톱 등급이 다르면 후보에서 뺀다.
_SERVER_CPU = re.compile(r"\b(EPYC|XEON|제온|스레드리퍼|THREADRIPPER)\b", re.IGNORECASE)


def _cpu_class_conflict(part: dict, title: str) -> bool:
    if str(part.get("category") or "").strip() != "CPU":
        return False
    want_server = bool(_SERVER_CPU.search(str(part.get("name") or "")))
    return want_server != bool(_SERVER_CPU.search(title))


# 카테고리별 제목 가드 — USB 메모리를 RAM 대체로 붙이는 류의 잡음을 후보 단계에서 뺀다.
# 주의: \bUSB\b 는 "USB메모리" 에 안 걸린다 — B 와 '메' 사이에 유니코드 단어 경계가 없다(실측).
_CATEGORY_TITLE_GUARD = {
    "RAM": re.compile(r"USB|OTG|메모리카드|SD카드|카드형", re.IGNORECASE),
}


def _category_guard(part: dict, title: str) -> bool:
    pattern = _CATEGORY_TITLE_GUARD.get(str(part.get("category") or "").strip())
    return bool(pattern and pattern.search(title))


async def _similar_substitute(part: dict, quotes: list[dict], rejects: list[tuple[dict, str]]) -> dict | None:
    """가격을 못 찾은 부품을 '최대한 비슷한 실제 제품'으로 채운다.

    요구(규격서 이름)와 무엇으로 대체했는지(product·matchReason)를 함께 돌려준다.
    후보: (1) 방금 받은 검색 견적 중 규격이 조금 다른 것, (2) 로컬 다나와 색인의
    가장 비슷한 상품(pcode 로 등재가 조회). 완제품·중고·묶음은 여기서도 제외한다.
    """
    candidates: list[dict] = []

    def add(quote: dict, score: int, reason: str) -> None:
        if not isinstance(quote.get("priceKrw"), int) or quote["priceKrw"] <= 0:
            return
        name = str(quote.get("name") or "")
        if _NOISE.search(name) or _cpu_class_conflict(part, name) or _category_guard(part, name):
            return
        candidates.append({"quote": quote, "score": score, "reason": reason})

    for quote, reason in rejects:
        if _SUB_BAD_REASON.search(reason):
            continue
        add(quote, _token_overlap(part["name"], str(quote.get("name") or "")), reason)

    index_hit = product_index.best_match(part["name"], part["category"], min_score=1)
    if index_hit:
        try:
            for quote in await quotes_by_pcode(index_hit["pcode"], deadline_ms=8000):
                add(quote, _token_overlap(part["name"], index_hit["name"]), "색인 유사 상품")
        except AiFailure:
            pass

    if not candidates:
        return None
    # 비슷한 정도가 같으면 싼 쪽 — "최대한 비슷한" 제품 중 등재가가 낮아야 원가가 과하게 잡히지 않는다.
    candidates.sort(key=lambda c: (-c["score"], c["quote"]["priceKrw"]))
    best = candidates[0]
    return {"quote": best["quote"], "matchReason": _substitute_note(best["reason"])}


async def _llm_substitute_ok(part: dict, candidate_name: str, candidate_price: int) -> dict | None:
    """대체 후보가 요구를 실제로 충족하는지 LLM에게 묻는다 — 충족하면 원가에 포함한다.

    규칙(part_resolver)이 정확 일치가 아니라는 이유로 뺀 후보 중에도 요구를 충족하는
    제품이 있다(실측: "LGA1700" 토큰이 제목에 없다고 완전히 맞는 메인보드가 탈락).
    LLM이 "충족"이라 하면 inferred 를 꺼서 백엔드 확정 원가에 들어가게 한다.
    **명시적으로 미충족(false)일 때만** 참고값으로 남긴다 — 실제 상품을 찾았는데
    판정 실패(None)로 빼면 "상품은 찾았는데 왜 제외해"가 재발한다.

    반환은 {"ok": bool, "cores": int|None} — cores 는 후보의 코어 수로, LLM의 ok 판정과
    별도로 호출 쪽이 요구 코어 수와 결정적으로 비교한다(실측: 12코어 요구에 9700X 8코어가
    ok 로 통과했다 — 코어 수 비교는 규칙이 맡는 게 낫다).
    """
    if not _quote_llm_enabled():
        return None
    prompt = (f"요구 부품: {part['name']}\n"
              f"규격서 근거: {part.get('evidence') or '(없음)'}\n"
              f"대체 후보: {candidate_name}\n"
              f"등재가: {candidate_price:,}원")
    try:
        model = await loaded_model()
        response = await lms_chat({
            "model": model,
            "messages": [
                {"role": "system", "content":
                 "너는 하드웨어 견적 심사관이다. 요구 부품의 규격과 대체 후보 제품을 보고, 후보가 "
                 '요구 규격을 충족하거나 동등 이상이면 {"ok": true, "cores": 후보 코어 수}, '
                 "명백히 미달이면 false 로 답해라. 애매하면 true 다 — 찾은 실제 상품을 애매하다는 "
                 "이유로 빼면 안 된다. cores 는 후보 CPU 의 코어 수 정수(CPU 가 아니면 0)다. "
                 "JSON 객체 하나로만 답해라."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0, "max_tokens": 100,
        })
        raw = str(response["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError, AiFailure):
        return None
    verdict: dict = {"ok": None, "cores": None}
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            guess = json.loads(raw[start:end + 1])
            ok_raw = str(guess.get("ok") or "").strip().lower()
            verdict["ok"] = True if ok_raw == "true" else (False if ok_raw == "false" else None)
            try:
                verdict["cores"] = int(guess.get("cores") or 0)
            except (TypeError, ValueError):
                verdict["cores"] = None
        except ValueError:
            pass
    if verdict["ok"] is None:
        low = raw.lower()
        if "true" in low:
            verdict["ok"] = True
        elif "false" in low:
            verdict["ok"] = False
    if verdict["ok"] is None:
        return None
    return verdict


def _required_cores(part: dict) -> int | None:
    """요구 문구의 코어 수 — "12코어"·"12 Core" 꼴. 없으면 None."""
    m = re.search(r"(\d{1,3})\s*코어|(\d{1,3})\s*core", str(part.get("name") or ""), re.IGNORECASE)
    return int(m.group(1) or m.group(2)) if m else None


def _substitute_approved(part: dict, verdict: dict | None) -> bool:
    """대체 채택 여부 — LLM 판정이 없으면 채택(실상품 우선), 명시적 false 면 기각.

    코어 수는 LLM 판정과 별도로 결정적으로 비교한다: LLM이 ok 라고 해도 후보 코어 수가
    요구보다 적으면 기각한다(실측: 12코어 요구에 라이젠 7 9700X 8코어가 ok 로 통과).
    """
    if verdict is None:
        return True
    if verdict["ok"] is False:
        return False
    required = _required_cores(part)
    if required is not None and verdict["cores"] is not None and verdict["cores"] > 0:
        return verdict["cores"] >= required
    return True


async def _price_part(part: dict) -> dict:
    hint = _CATEGORY_HINT.get(part["category"], "")
    identity = derive_product_identity(part["name"])
    # 약한 신원(모델 없는 순수 사양)은 긴 사양 문구를 검색어로 쓰면 오히려 안 걸린다 —
    # 핵심 스펙("7.68TB") + 카테고리 힌트만 남긴다(실측: "7.68TB 2.5in ..." → 1건,
    # "7.68TB NVMe" → 24건). 모델이 뚜렷하면 원래 이름 그대로 쓴다.
    if identity["strong"]:
        query = f"{part['name']} {hint}".strip() if hint and hint not in part["name"] else part["name"]
    else:
        core = " ".join(identity.get("specs") or []) or part["name"]
        query = f"{core} {hint}".strip() if hint and hint not in core else core

    # pcode 직결 — 탐색이 다나와 상품 링크(pcode)를 찾아줬으면 검색 대신 상품을 지목한다.
    # 검색 결과 노이즈(완제품·중고) 없이 등재가가 나와서 part_resolver 통과율이 올라간다.
    # pcode 조회가 실패하면 원래 검색 경로로 떨어진다(치명적이지 않다).
    links = (part.get("discovery") or {}).get("shopLinks") or []
    pcode = next((str(link.get("sourceId") or "") for link in links
                  if link.get("source") == "danawa" and str(link.get("sourceId") or "").isdigit()), "")
    quotes: list[dict] = []
    if pcode:
        try:
            quotes = await quotes_by_pcode(pcode, deadline_ms=8000)
        except AiFailure:
            quotes = []
    if not quotes:
        result = await resolve_price({"itemName": query, "deadlineMs": 8000})
        quotes = result.get("quotes") or []
    if not quotes:
        # 긴 사양 문구는 쇼핑몰 검색창에서 통째로 안 걸린다(실측: "인텔 LGA1700 소켓 ..." 0건).
        # 핵심 스펙으로 한 번 더 친다 — 이 견적들은 대체 후보·LLM 심사의 재료가 된다.
        core = " ".join(identity.get("specs") or []) or part["name"]
        retry_query = f"{core} {hint}".strip() if hint and hint not in core else core
        if retry_query != query:
            result = await resolve_price({"itemName": retry_query, "deadlineMs": 8000})
            quotes = result.get("quotes") or []

    # ITMAYA 제거(2026-08-14) — 리졸버가 색인 행을 흘려도 추정에는 안 쓴다.
    quotes = [q for q in quotes if str(q.get("source") or "") != "itmaya"]

    # 사양 대조(part_resolver) — 안전장치만 남긴다: 완제품 PC·중고·렌탈·액세서리,
    # 다른 용량, 묶음(개수×용량). "같은 물건인가"의 미세한 판단은 아래 LLM 심사가 맡는다.
    verified = []
    rejects: list[tuple[dict, str]] = []
    for q in quotes:
        if not isinstance(q.get("priceKrw"), int) or q["priceKrw"] <= 0:
            continue
        title = str(q.get("name") or "")
        # 등급 가드 — 데스크톱 요구에 서버 CPU, RAM 요구에 USB 메모리 등은 검증 전에 뺀다.
        if _cpu_class_conflict(part, title) or _category_guard(part, title):
            continue
        verdict = quote_matches_identity({"title": title, "price": q["priceKrw"], "url": q.get("url")}, identity)
        if os.getenv("QUOTE_DEBUG") == "1":
            print(f"[quote-debug] {part['name'][:40]} | {q.get('source')} {q.get('priceKrw')} | {verdict['ok']} {verdict['reason']} | {title[:70]}", flush=True)
        if verdict["ok"]:
            verified.append(q)
        else:
            rejects.append((q, verdict["reason"]))

    row_inferred = not identity["strong"]

    # LLM 심사 — **기본 판단 경로**. 약한 신원(모델 없는 사양)은 항상, 강한 신원도
    # 가격 범위가 벌어져 있으면(묶음·노이즈 의심) 후보 중 "같은 물건"을 고르게 한다.
    # 규칙 통과를 못 한 약한 신원은 중고·완제품만 제외한 원본을 LLM에게 맡긴다.
    # LLM 이 고르면 참고값이 아니라 판정값이다 — inferred 를 끈다.
    llm_pool = verified
    if not llm_pool and not identity["strong"]:
        llm_pool = [q for q in quotes
                    if isinstance(q.get("priceKrw"), int) and q["priceKrw"] > 0
                    and not _NOISE.search(q.get("name") or "")
                    and not _cpu_class_conflict(part, str(q.get("name") or ""))
                    and not _category_guard(part, str(q.get("name") or ""))]
    llm_decided = False
    if _quote_llm_enabled() and len(llm_pool) >= 2:
        lo = min(q["priceKrw"] for q in llm_pool)
        hi = max(q["priceKrw"] for q in llm_pool)
        if (not identity["strong"]) or (lo > 0 and hi / lo >= _quote_llm_min_spread()):
            picked = await _llm_pick_quotes(part, llm_pool)
            if picked is not None:
                # None(판정 실패)만 규칙 결과를 유지한다. [] 는 "같은 물건 없음" 판정 —
                # 그때는 완화 경로로 잡음을 되살리지 않는다.
                llm_decided = True
                verified = picked
                row_inferred = False

    # LLM 이 꺼져 있거나(QUOTE_LLM=0) 판정에 실패했을 때만 — 약한 신원은 종전대로 완화 경로(참고값).
    if not verified and not identity["strong"] and not llm_decided:
        verified = [q for q in quotes
                    if isinstance(q.get("priceKrw"), int) and q["priceKrw"] > 0 and not _NOISE.search(q.get("name") or "")]

    if not verified:
        # 다나와가 못 찾아도 가격 없는 행은 남기지 않는다.
        # 1) 규격이 조금 다른 실제 제품(대체) — 무엇을 요구했고 무엇으로 바꿨는지 함께 남긴다.
        #    LLM이 "요구 충족"이라 하면 참고값이 아니라 원가에 들어간다(inferred=False).
        substitute = await _similar_substitute(part, quotes, rejects)
        if substitute:
            q = substitute["quote"]
            verdict = await _llm_substitute_ok(part, str(q.get("name") or ""), q["priceKrw"])
            # 명시적 미충족(false)·코어 수 부족만 참고값으로 뺀다 — 그 외엔 원가에 넣는다.
            approved = _substitute_approved(part, verdict)
            return {"category": part["category"], "option": part["name"],
                    "product": q["name"], "requirement": part["name"],
                    "qty": part["qty"], "low": q["priceKrw"], "high": q["priceKrw"],
                    "inferred": not approved, "role": "part",
                    "source": q.get("source", "danawa"),
                    "substitute": True,
                    "matchReason": substitute["matchReason"] if approved
                        else substitute["matchReason"] + " — 요구 미충족 판정으로 참고값",
                    "url": q.get("url"),
                    "named": bool(part.get("named")), "evidence": part.get("evidence", ""),
                    "discoveredFrom": part.get("discoveredFrom"), "discovery": part.get("discovery"),
                    "searchUnavailable": bool(part.get("searchUnavailable"))}
        # 2) 그래도 없으면 LLM 시장가 추정 — 어떤 유사 제품을 기준으로 삼았는지 같이 받는다.
        estimate = await _llm_price_estimate(part)
        if estimate:
            # LLM이 지목한 유사 제품명으로 실제 검색을 한 번 더 친다 — 추정 숫자보다 등재가가 낫다.
            similar_name = str(estimate.get("similar") or "").strip()
            if similar_name and similar_name != "시장가 추정(LLM)":
                # 여러 모델을 나열해 오면 첫 제품만 쓴다 — "A / B / C" 를 한 검색어로 치면
                # 우산 같은 잡음이 최저가 자리에 앉는다(실측).
                candidate_model = similar_name.split("/")[0].strip()
                sim_identity = derive_product_identity(candidate_model)
                try:
                    retry = await resolve_price({"itemName": candidate_model, "deadlineMs": 8000})
                except AiFailure:
                    retry = None
                # 지목된 모델과 실제로 맞는 견적만 — 완제품·중고·묶음 등 안전장치는 그대로.
                verified_sim = [q for q in (retry.get("quotes") if retry else []) or []
                                if isinstance(q.get("priceKrw"), int) and q["priceKrw"] > 0
                                and str(q.get("source") or "") != "itmaya"
                                and not _NOISE.search(q.get("name") or "")
                                and not _cpu_class_conflict(part, str(q.get("name") or ""))
                                and not _category_guard(part, str(q.get("name") or ""))
                                and quote_matches_identity(
                                    {"title": q.get("name"), "price": q["priceKrw"], "url": q.get("url")},
                                    sim_identity)["ok"]]
                if verified_sim:
                    # 저가 순으로 최대 3개 후보를 LLM에게 확인해 요구를 충족하는 것을 고른다.
                    # 지목 LLM이 코어 수가 부족한 제품을 잘못 지목하는 일이 있다(실측: 12코어 요구에 7700X).
                    candidates = sorted(verified_sim, key=lambda x: x["priceKrw"])[:3]
                    for q in candidates:
                        verdict = await _llm_substitute_ok(part, str(q.get("name") or ""), q["priceKrw"])
                        if _substitute_approved(part, verdict):
                            return {"category": part["category"], "option": part["name"],
                                    "product": q["name"], "requirement": part["name"],
                                    "qty": part["qty"], "low": q["priceKrw"], "high": q["priceKrw"],
                                    "inferred": False, "role": "part",
                                    "source": q.get("source", "danawa"),
                                    "substitute": True,
                                    "matchReason": "등재가 미발견 — LLM이 지목한 유사 제품으로 검색",
                                    "url": q.get("url"),
                                    "named": bool(part.get("named")), "evidence": part.get("evidence", ""),
                                    "discoveredFrom": part.get("discoveredFrom"), "discovery": part.get("discovery"),
                                    "searchUnavailable": bool(part.get("searchUnavailable"))}
                    # 전부 미충족 — 가장 싼 것을 참고값으로만 남긴다(확정 원가 제외).
                    q = candidates[0]
                    return {"category": part["category"], "option": part["name"],
                            "product": q["name"], "requirement": part["name"],
                            "qty": part["qty"], "low": q["priceKrw"], "high": q["priceKrw"],
                            "inferred": True, "role": "part",
                            "source": q.get("source", "danawa"),
                            "substitute": True,
                            "matchReason": "등재가 미발견 — 유사 제품 요구 미충족으로 참고값",
                            "url": q.get("url"),
                            "named": bool(part.get("named")), "evidence": part.get("evidence", ""),
                            "discoveredFrom": part.get("discoveredFrom"), "discovery": part.get("discovery"),
                            "searchUnavailable": bool(part.get("searchUnavailable"))}
            # 여기까지 왔으면 실제 상품이 아니다 — 추정값을 참고값으로만 남긴다(확정 원가 제외).
            return {"category": part["category"], "option": part["name"],
                    "product": estimate["similar"], "requirement": part["name"],
                    "qty": part["qty"], "low": estimate["low"], "high": estimate["high"],
                    "inferred": True, "role": "part", "source": "llm-estimate",
                    "substitute": True, "matchReason": "등재가 미발견 — 유사 제품 기반 LLM 시장가 추정",
                    "named": bool(part.get("named")), "evidence": part.get("evidence", ""),
                    "discoveredFrom": part.get("discoveredFrom"), "discovery": part.get("discovery"),
                    "searchUnavailable": bool(part.get("searchUnavailable"))}
        # 그래도 가격을 못 찾은 행은 어떤 소스가 이겼는지 알 수 없다 — source=None(가격도 None).
        return {"category": part["category"], "option": part["name"], "product": None,
                "qty": part["qty"], "low": None, "high": None, "inferred": True,
                "role": "part", "source": None}
    prices = sorted(q["priceKrw"] for q in verified)
    cheapest = min(verified, key=lambda q: q["priceKrw"])
    # 최저가를 낸 소스를 그대로 실어 _merge_estimates 의 priceSource 가 정확하게 나오게 한다
    # (멀티소스 리졸버는 danawa·enuri 어디서든 후보를 줄 수 있다 — itmaya 는 위에서 걸러냈다).
    return {"category": part["category"], "option": part["name"], "product": cheapest["name"],
            "qty": part["qty"], "low": prices[0], "high": prices[-1],
            "inferred": row_inferred, "role": "part",
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


# ── 정규화 (웹 단일 경로) ────────────────────────────────────────────────────


def _sum_priced(rows: list[dict]) -> tuple[int, int]:
    low = sum(r["low"] * r["qty"] for r in rows if r.get("low") is not None)
    high = sum((r["high"] if r.get("high") is not None else r["low"]) * r["qty"]
               for r in rows if r.get("low") is not None)
    return low, high


def _merge_estimates(web: dict) -> dict:
    """웹 부품 수집 결과를 응답 규격으로 정규화한다.

    ITMAYA 색인 경로는 제거됐다(2026-08-14) — stale 옵션 행(예: 64GB)이 dedup 키를
    선점해 웹의 정확한 견적(128GB)을 잘라먹던 문제 때문에 색인·웹 병합을 걷어냈다.
    """
    web_rows = list(web.get("breakdown") or []) if web.get("matched") else []
    if not web_rows:
        reason = web.get("reason") or "부품을 식별하지 못했습니다."
        out = {"matched": False, "reason": reason}
        if web.get("gpuCount"):
            out["gpuCount"] = web["gpuCount"]
        return out

    low, high = _sum_priced(web_rows)
    # 가격을 낸 행의 소스만 센다 — 가격 없는 행(source=None)은 priceSource 를 흐리면 안 된다.
    sources = {r.get("source") for r in web_rows if r.get("source")}
    price_source = "hybrid" if len(sources) > 1 else (next(iter(sources), None) or "danawa")

    out = {
        "matched": True, "low": low, "high": high, "mid": (low + high) // 2,
        "gpuCount": sum(r["qty"] for r in web_rows if str(r.get("category") or "").strip() == "GPU"),
        "hasBase": False, "breakdown": list(web_rows),
        "allPriced": all(r.get("low") is not None for r in web_rows),
        "currency": "KRW", "priceSource": price_source,
    }
    if isinstance(web.get("prebuilt"), dict):
        out["prebuilt"] = web["prebuilt"]   # 완제품 판정·후보는 웹 경로가 만든다
    return out


async def estimate_unit_cost(payload: dict) -> dict:
    """규격서 텍스트 → 원가 추정. 웹 수집 단일 경로(ITMAYA 색인 제거 — 2026-08-14)."""
    spec_text = str(payload.get("specText") or "").strip()
    if not spec_text:
        return {"matched": False, "reason": "규격서 텍스트가 없습니다."}

    web = await _estimate_from_web(spec_text)
    return _merge_estimates(web)


if __name__ == "__main__":
    # 정규화(웹 단일 경로) 스모크 — LLM 없이 순수 함수만 검증한다.
    web = {"matched": True, "breakdown": [
        {"category": "GPU", "option": "NVIDIA H200 141GB", "product": "H200", "qty": 8,
         "low": 5_000_000, "high": 5_100_000, "role": "part", "source": "danawa"},
        {"category": "SSD", "option": "삼성 990 PRO 4TB", "product": "990 PRO", "qty": 2,
         "low": 500_000, "high": 600_000, "role": "part", "source": "danawa"},
    ], "prebuilt": {"isPrebuilt": False}}
    m = _merge_estimates(web)
    assert m["matched"] and not m["hasBase"], m
    assert m["gpuCount"] == 8 and m["priceSource"] == "danawa", m
    assert m["low"] == 5_000_000 * 8 + 500_000 * 2, m["low"]
    assert "prebuilt" in m
    # 웹 실패도 그대로 전달된다
    fail = _merge_estimates({"matched": False, "reason": "부품 없음"})
    assert not fail["matched"] and fail["reason"] == "부품 없음", fail
    print("app/estimate.py: OK")
