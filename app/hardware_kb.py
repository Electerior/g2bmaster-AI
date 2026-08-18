"""컴퓨터 하드웨어 전문 RAG — 사양 → 모델명 식별의 로컬 지식베이스(Phase 2).

동기: 외부 검색(SearXNG)은 차단에 취약하고, Wikipedia API 도 모델명이 없는
부품(RAM·파워)은 못 잡는다. 여기서는 **로컬 코퍼스** 를 쓴다:

  - Wikipedia GPU·CPU 시리즈 문서의 모델별 사양표(`scripts/build_hardware_kb.py` 가 수집)
  - ITMAYA GPU서버 가격표의 기종명

조회는 두 단계다:
  1. **결정적 스코어링** — 사양 숫자(코어 수·메모리·TDP)를 코퍼스 사양 문자열과
     대조. 네트워크도 ML 도 필요 없다. 대부분의 GPU·CPU 가 여기서 끝난다.
  2. **LLM 판정(RAG reader)** — 결정적 단계가 못 정하면 상위 후보를 컨텍스트로
     로컬 LLM 에게 "이 사양은 무슨 모델인가"를 묻고, 근거를 인용하게 한다.

임베딩 경로는 선택이다 — ML 스택이 깔려 있고 벡터 파일이 있으면 코사인 유사도로
후보를 거른다. 없어도 1·2단계만으로 동작한다(차단 불가·오프라인).

가격은 여기서 만들지 않는다 — 식별만 하고 가격은 danawa·enuri 파서가 맨다
(`backend-price-api.md` 경계 그대로).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from . import wiki as _wiki
from .errors import AiFailure

KB_PATH = Path(__file__).resolve().parent.parent / "data" / "hardware-kb.jsonl"
VECTORS_PATH = Path(__file__).resolve().parent.parent / "data" / "hardware-kb-vectors.json"

#: 결정적 스코어로 "찾았다"고 말하는 최소 점수. 숫자 토큰 2점(4자리 이상)·1점(2~3자리) 기준.
MIN_SCORE = 3
TOP_K = 5

#: 카테고리 정규화 — estimate.py 의 _CANON_CAT 축소본(import 하면 순환이 생긴다).
_CANON = {
    "gpu": "gpu", "vga": "gpu", "그래픽카드": "gpu", "system": "gpu",
    "cpu": "cpu", "processor": "cpu",
    "ram": "ram", "memory": "ram", "메모리": "ram",
    "storage": "storage", "ssd": "storage", "hdd": "storage", "저장장치": "storage",
}


def canon_category(category: str) -> str:
    return _CANON.get(str(category or "").strip().lower(), str(category or "").strip().lower())


def _corpus_mtime() -> float:
    try:
        return KB_PATH.stat().st_mtime
    except OSError:
        return 0.0


_corpus: tuple[float, list[dict]] | None = None


def load_corpus(force: bool = False) -> list[dict]:
    """코퍼스 {category, model, text} 목록. 파일 mtime 이 바뀌면 다시 읽는다."""
    global _corpus
    mtime = _corpus_mtime()
    if not force and _corpus is not None and _corpus[0] == mtime:
        return _corpus[1]
    try:
        rows: list[dict] = []
        with KB_PATH.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("model") and row.get("text"):
                    rows.append(row)
        _corpus = (mtime, rows)
        return rows
    except OSError:
        _corpus = (0.0, [])
        return []


def enabled() -> bool:
    """코퍼스가 준비돼 있는가. 없으면 체인에서 이 소스만 건너뛴다(치명적이지 않다)."""
    return KB_PATH.exists() and bool(load_corpus())


def retrieve(spec_text: str, category: str = "", k: int = TOP_K) -> list[tuple[int, dict]]:
    """사양 → 코퍼스 상위 k 후보. 결정적 숫자 토큰 스코어링(ML 불필요)."""
    nums = _wiki.spec_numbers(spec_text)
    want = canon_category(category)
    scored: list[tuple[int, dict]] = []
    for row in load_corpus():
        row_cat = canon_category(row.get("category", ""))
        if want and want != row_cat:
            continue
        score = _wiki._score_model(nums, row.get("text", ""))
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda pair: (-pair[0], pair[1].get("model", "")))
    return scored[:k]


async def _llm_identify(spec_text: str, category: str, candidates: list[dict]) -> str | None:
    """RAG reader — 상위 후보를 근거로 로컬 LLM 에게 모델명을 묻는다.

    LLM 이 없거나 죽었으면 None(치명적이지 않다). 후보가 빈약해도 안 부른다 —
    근거 없는 추측을 모델명으로 승격시키면 다나와 검색이 틀린 물건을 잡는다.
    """
    if not candidates or os.getenv("KB_LLM", "1") == "0":
        return None
    try:
        from .llm.client import loaded_model, lms_chat
        model = await loaded_model()
        if not model:
            return None
    except (ImportError, AiFailure):
        return None
    evidence = "\n".join(
        f"- {c['model']}: {str(c.get('text'))[:300]}" for c in candidates[:TOP_K])
    response = await lms_chat({
        "model": model,
        "messages": [
            {"role": "system", "content":
             "너는 하드웨어 사양 전문가다. 주어진 사양을 아래 후보와 대조해 정확한 모델명을 하나 고른다. "
             "후보에 맞는 것이 없거나 확신이 없으면 '불확실'이라고만 답하라. 답은 모델명 한 줄만."},
            {"role": "user", "content":
             f"사양: {spec_text[:400]}\n분류: {category or '하드웨어'}\n\n후보:\n{evidence}"},
        ],
        "temperature": 0, "max_tokens": 120,
    })
    try:
        answer = str(response["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return None
    answer = re.sub(r"^.*?:\s*", "", answer)      # "모델명: RTX 5080" 꼴 허용
    if not answer or "불확실" in answer or "없" in answer[:6]:
        return None
    return answer[:120]


async def identify(spec_text: str, category: str = "", deadline_s: float = 8.0) -> dict[str, Any]:
    """사양 → 모델명. 발견 결과는 discover 계약 모양이다.

    결정적 스코어가 임계를 넘으면 즉시 확정하고, 못 넘으면 LLM 판정을 한 번 시도한다.
    전부 실패해도 not-found 일 뿐 — 상위가 다음 소스(Wikipedia API)를 시도한다.
    """
    query = re.sub(r"\s+", " ", str(spec_text or "")).strip()[:200]
    if not query or not enabled():
        return {"query": query, "model": None, "candidates": [], "shopLinks": [],
                "status": "not-found", "source": "hardware-kb"}
    scored = retrieve(query, category)
    if not scored:
        return {"query": query, "model": None, "candidates": [], "shopLinks": [],
                "status": "not-found", "source": "hardware-kb"}
    best_score, best = scored[0]
    candidates = [{"title": row.get("model", ""), "url": "", "model": row.get("model", ""),
                   "score": score} for score, row in scored]

    if best_score >= MIN_SCORE:
        return {"query": query, "model": best["model"], "modelVotes": best_score,
                "candidates": candidates, "shopLinks": [], "status": "found",
                "source": "hardware-kb"}

    # 2차 — LLM RAG 판정. 실패해도 조용히 not-found 로 떨어진다(치명적이지 않다).
    try:
        model = await _llm_identify(query, category, [row for _, row in scored])
    except BaseException:  # noqa: BLE001 — LLM 장애는 식별 실패가 아니다
        model = None
    if model:
        return {"query": query, "model": model, "modelVotes": best_score,
                "candidates": candidates, "shopLinks": [], "status": "found",
                "source": "hardware-kb-llm"}

    return {"query": query, "model": None, "candidates": candidates, "shopLinks": [],
            "status": "not-found", "source": "hardware-kb"}
