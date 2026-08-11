"""아이티마야(ITMAYA) GPU서버 가격표 색인 — 오래된(stale) 단가 소스.

`data/ITMAYA_GPU서버_가격표.xlsx` '전체옵션' 시트를 색인한다. 원본 색인 로더를
estimate.py 에서 이리로 옮겨 왔다(estimate.py 는 `load_index`/`_num` 을 여기서 import).
의존은 pricecommon + openpyxl 뿐이라 소스 모듈끼리 순환이 없다.

두 쓰임:
  - `load_index()` / `_num()` — estimate.py 의 규칙 매칭(GPU서버 단가 합산)이 그대로 쓴다.
  - `search()` — 멀티소스 리졸버가 쓴다. 서버급 질의만 ITMAYA 행을 끌어오고, 소비자 질의는
    [] 를 돌려준다(점수 게이트 + 서버 신호 게이트).

가격표는 파일이라 신선도가 없다: basis="stale", stale=True, collectedAt = xlsx 파일 mtime(KST).
xlsx 가 없어도 치명적이지 않다 — [] 를 돌려준다.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .pricecommon import KST, MAX_QUOTES, make_quote, now_kst

SOURCE = "itmaya"
#: 최선-노력 URL(카탈로그에 상품별 링크가 없다). VERIFY LIVE.
ITMAYA_URL = "https://www.itmaya.com/"
_XLSX = Path(__file__).resolve().parent.parent / "data" / "ITMAYA_GPU서버_가격표.xlsx"
_index_cache: dict | None = None

#: 검색 행 채택 최소 점수. 숫자 토큰(용량·모델)은 2점이므로 사실상 "모델/용량이 걸린 행"만.
_MIN_SEARCH_SCORE = 2

# ── 서버 신호 게이트 (estimate.py 의 최소본을 복제 — import 하면 순환이 생긴다) ──────
_GPU_SERVER = re.compile(
    r"tesla|\bh100\b|\bh200\b|\ba100\b|\ba30\b|\ba40\b|\ba10\b|\bl40s?\b|\bl4\b|"
    r"rtx\s?40\d0|rtx\s?50\d0|rtx\s*pro|blackwell|quadro|gpgpu|gpu\s*서버|가속\s*서버|딥\s*러닝|"
    r"deep\s*learning|추론\s*서버|ai\s*(?:학습|추론|서버)|gpu\s*server|gpu\s*workstation|"
    r"워크스테이션|workstation|xeon|epyc",
    re.IGNORECASE,
)
_NON_SERVER = re.compile(
    r"노트북|랩탑|laptop|태블릿|tablet|일체형|올인원|all-?in-?one|모니터|프린터|복합기|스캐너",
    re.IGNORECASE,
)
_SERVER_CTX = re.compile(
    r"서버|server|랙마운트|랙\s*마운트|rack\s*mount|데이터\s*센터|워크스테이션", re.IGNORECASE)

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


def _is_gpu_server_text(text: str) -> bool:
    if _NON_SERVER.search(text) and not _SERVER_CTX.search(text):
        return False
    return bool(_GPU_SERVER.search(text))


def _num(v) -> int | None:
    if v is None:
        return None
    try:
        return int(float(str(v).replace(",", "")))
    except (ValueError, TypeError):
        return None


def load_index() -> dict | None:
    """ITMAYA '전체옵션' 시트 → {byCategory: {category: [{product,name,low,high}]}}. 실패하면 None."""
    global _index_cache
    if _index_cache is not None:
        return _index_cache or None
    try:
        import openpyxl
        wb = openpyxl.load_workbook(_XLSX, read_only=True, data_only=True)
        ws = wb["전체옵션"]
        by_category: dict[str, list[dict]] = {}
        for rn, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if rn < 5:
                continue
            product = str(row[0] or "").strip()
            category = str(row[1] or "").strip()
            name = str(row[2] or "").strip()
            low = _num(row[3])
            high = _num(row[4])
            if not category or not name or low is None:
                continue
            by_category.setdefault(category, []).append(
                {"product": product, "name": name, "low": low, "high": low if high is None else high})
        _index_cache = {"byCategory": by_category}
    except Exception:  # noqa: BLE001 — 색인이 없으면 이 소스만 [] 로 조용히 빠진다
        _index_cache = {}
    return _index_cache or None


def _xlsx_mtime_kst() -> str:
    try:
        return datetime.fromtimestamp(_XLSX.stat().st_mtime, KST).replace(microsecond=0).isoformat()
    except OSError:
        return now_kst()


async def search(item_name: str, deadline_ms: int | None = None) -> list[dict]:
    """서버급 질의 → ITMAYA 색인 행(stale). 소비자 질의·색인 부재 → []. 치명적이지 않다.

    deadline_ms 는 계약 통일을 위해 받되 쓰지 않는다(파일 색인이라 네트워크가 없다).
    """
    index = load_index()
    if not index:
        return []
    text = str(item_name or "")
    if not _is_gpu_server_text(text):
        return []
    qset = set(_tokens(text))
    if not qset:
        return []

    collected = _xlsx_mtime_kst()
    quotes: list[dict] = []
    for category, rows in index["byCategory"].items():
        for rn, opt in enumerate(rows):
            if _score_name(qset, opt["name"]) < _MIN_SEARCH_SCORE:
                continue
            low = opt.get("low")
            quotes.append(make_quote(
                source=SOURCE,
                sourceId=f"{category}|{opt['product']}|{rn}",
                url=ITMAYA_URL,
                name=opt["name"],
                priceKrw=low if isinstance(low, int) and low > 0 else None,
                spec=category,
                basis="stale",
                stale=True,
                collectedAt=collected,
            ))
    # 가격 있는 것 우선, 그 안에서 저가순.
    quotes.sort(key=lambda q: (q["priceKrw"] is None, q["priceKrw"] or 0))
    return quotes[:MAX_QUOTES]


if __name__ == "__main__":  # 실제 xlsx(app/../data)에 대해 검증한다
    import asyncio

    idx = load_index()
    assert idx and "System" in idx["byCategory"], "ITMAYA 색인 로드 실패"
    assert _num("1,234,000") == 1234000 and _num(None) is None and _num("x") is None

    async def _check() -> None:
        # 서버급 질의 → ITMAYA 행
        qs = await search("GPU 서버 ESC8000-E12 RTX PRO 6000 96GB")
        assert qs, "서버급 질의는 ITMAYA 행을 돌려줘야 한다"
        assert all(q["source"] == "itmaya" for q in qs)
        assert all(q["basis"] == "stale" and q["stale"] is True for q in qs)
        assert all(len(q["sourceId"].split("|")) == 3 for q in qs), qs[0]["sourceId"]
        assert all(q["collectedAt"].endswith("+09:00") for q in qs)
        assert all(q["priceKrw"] is None or isinstance(q["priceKrw"], int) for q in qs)
        assert all(q["priceKrw"] != 0 for q in qs), "0 은 싣지 않는다(모르면 None)"
        # 소비자 질의 → []
        assert await search("삼성 노트북 갤럭시북 사무용") == []
        assert await search("가정용 잉크젯 프린터 복합기") == []
        assert await search("") == []

    asyncio.run(_check())
    print("app/itmaya.py: OK")
