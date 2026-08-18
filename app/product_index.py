"""다나와 인기 상품 로컬 인덱스 — 자체 크롤러로 구축하는 상품 검색층(Phase 3).

동기: 상용 SERP API 없이, 우리 서버에서 "이 모델명의 다나와 상품(pcode)이 뭐지?"를
풀고 싶다. 다나와 **카테고리 목록 페이지**(인기순 30건)를 polite rate 로 긁어
로컬 인덱스로 눌러앉힌다.

용도 둘:
  1. **탐색 체인의 보강** — 모델명("RTX 5080")이 나오면 이 인덱스에서 정확한
     다나와 상품명·pcode 를 찾아 `shopLinks` 로 실어 준다. 다나와 pcode 는
     가격 파서가 화이트리스트로 받는 형태다(`backend-price-api.md` §4.5).
  2. **pcode 직결** — estimate 가 검색 대신 상품을 지목해 등재가를 읽는다.

인덱스는 파일(`data/product-index.jsonl`)이라 신선도는 크롤 시점 기준이다.
구축: `scripts/build_product_index.py`(`make pindex`). 크롤은 **반드시 polite rate**
(카테고리당 1초 이상 간격) 로 돌린다 — 다나와가 IP 를 막으면 이 소스가 통째로 죽는다.

가격은 여기서 만들지 않는다 — 상품명·pcode 만 찾고, 가격은 기존 파서가 맨다.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import time
from pathlib import Path
from typing import Any

from . import browser_fetch
from .pricecommon import clean_html, fetch

INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "product-index.jsonl"
DANAWA_LIST = "https://prod.danawa.com/list/"
DANAWA_PRODUCT = "https://prod.danawa.com/info/"

#: (cate, 카테고리 라벨). GPU·CPU·RAM·SSD·메인보드·파워 — 규격서에서 흔한 부품.
CATEGORIES: list[tuple[str, str]] = [
    ("112747", "CPU"),
    ("112751", "메인보드"),
    ("112752", "RAM"),
    ("112753", "GPU"),
    ("112760", "SSD"),
    ("112777", "파워"),
]

#: 목록 페이지의 상품 블록. price.py 의 _parse_products 와 같은 구조다(id="productItemNNN").
#: 목록에는 인기순위 <strong> 이 이름 <a> 앞에 껴 있어서 `<a>` 를 바로 요구하면 안 된다.
_ITEM_SPLIT = re.compile(r'id="productItem(\d+)"')
_NAME = re.compile(r'class="prod_name">.*?<a[^>]*>(.*?)</a>', re.S)


def parse_list_page(html: str, category: str) -> list[dict]:
    """다나와 목록 HTML → {category, name, pcode, url}. 가격은 안 읽는다."""
    parts = _ITEM_SPLIT.split(html)
    rows: list[dict] = []
    for i in range(1, len(parts) - 1, 2):
        pcode = parts[i]
        name_match = _NAME.search(parts[i + 1])
        if not name_match:
            continue
        name = clean_html(name_match.group(1))
        if not name:
            continue
        rows.append({"category": category, "name": name, "pcode": pcode,
                     "url": f"{DANAWA_PRODUCT}?pcode={pcode}"})
    return rows


async def crawl_category(cate: str, category: str, deadline_s: float = 15.0) -> list[dict]:
    """카테고리 목록 첫 페이지(인기순)를 긁는다. 실패해도 빈 목록 — 치명적이지 않다.

    차단이 감지되면 브라우저 폴백으로 한 번 재시도한다(평시엔 httpx).
    """
    try:
        html = await fetch(DANAWA_LIST, {"cate": cate}, deadline_s)
        if browser_fetch.looks_blocked(html):
            html = await browser_fetch.fetch_html(DANAWA_LIST, {"cate": cate},
                                                  deadline_s=max(deadline_s, 15.0))
        return parse_list_page(html, category)
    except Exception:  # noqa: BLE001 — 한 카테고리 실패가 인덱스 전체 실패가 아니다
        return []


# ── 로컬 인덱스 조회 ─────────────────────────────────────────────────────────
_corpus: tuple[float, list[dict]] | None = None


def _index_mtime() -> float:
    try:
        return INDEX_PATH.stat().st_mtime
    except OSError:
        return 0.0


def load_index(force: bool = False) -> list[dict]:
    global _corpus
    mtime = _index_mtime()
    if not force and _corpus is not None and _corpus[0] == mtime:
        return _corpus[1]
    try:
        rows: list[dict] = []
        with INDEX_PATH.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("name") and row.get("pcode"):
                    rows.append(row)
        _corpus = (mtime, rows)
        return rows
    except OSError:
        _corpus = (0.0, [])
        return []


def enabled() -> bool:
    return INDEX_PATH.exists() and bool(load_index())


_TOKEN_SPLIT = re.compile(r"[^a-z0-9가-힣]+")


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(str(text or "").lower()) if len(t) >= 2]


def _score_name(qset: set[str], name: str) -> int:
    score = 0
    for token in set(_tokens(name)):
        if token in qset:
            score += 2 if token[0].isdigit() else 1
    return score


def search(model: str, category: str = "", k: int = 8) -> list[tuple[int, dict]]:
    """모델명 → 상품 후보. 토큰 중복 점수(숫자 가중). 카테고리 필터는 라벨 일치만 받는다."""
    qset = set(_tokens(model))
    if not qset:
        return []
    scored: list[tuple[int, dict]] = []
    for row in load_index():
        if category and row.get("category") != category:
            continue
        score = _score_name(qset, row["name"])
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
    return scored[:k]


def lookup(model: str, category: str = "", min_score: int = 2, k: int = 3) -> list[dict]:
    """모델명 → 화이트리스트 상품 링크(다나와 pcode). discover 계약의 shopLinks 꼴.

    "RTX 5080" → [{"source":"danawa","sourceId":"...","url":"...","title":"MSI 지포스 RTX 5080..."}]
    """
    hits = search(model, category, k=k * 3)
    out: list[dict] = []
    seen: set[str] = set()
    for score, row in hits:
        if score < min_score or row["pcode"] in seen:
            continue
        seen.add(row["pcode"])
        out.append({"source": "danawa", "sourceId": row["pcode"], "url": row["url"],
                    "title": row["name"]})
        if len(out) >= k:
            break
    return out


def best_match(model: str, category: str = "", min_score: int = 2) -> dict | None:
    """모델명과 가장 잘 맞는 상품 하나 — 발견 결과에 실을 모델명 승격용."""
    hits = search(model, category, k=1)
    if not hits or hits[0][0] < min_score:
        return None
    return hits[0][1]
