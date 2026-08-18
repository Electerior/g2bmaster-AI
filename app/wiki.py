"""Wikipedia 사양→모델 탐색 — SearXNG 의 1차 대체(Phase 1).

쇼핑몰 검색창은 사양을 못 읽고, SearXNG 는 상위 엔진 차단에 취약하다(discover.py 실측).
Wikipedia 는 **검색 엔진이 아니라 공개 API**라 CAPTCHA·요청률 차단이 거의 없고,
GPU·CPU 시리즈 문서에 모델별 사양표가 있다. 그래서 사양 숫자(코어 수·메모리·TDP)를
표의 열과 대조해 모델명을 특정한다.

가격은 만들지 않는다 — `discover.py` 계약과 같다. 이 모듈의 산출물은
`{query, model, candidates, status}` 뿐이다. 가격은 여전히 danawa·enuri 파서가 맨다.

차단·오류는 전부 "not-found" 로 흘린다 — 위키가 죽었다고 파이프라인이 죽으면 안 되고,
상위(discover.py)가 SearXNG 등 다음 소스를 시도한다.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .errors import AiFailure

TIMEOUT_S = 8.0
_MAX_RESULTS = 5

_API = "https://{lang}.wikipedia.org/w/api.php"

#: 문서 안에서 "모델명"으로 읽을 덩어리. 시리즈 문서 표의 열 머리가 보통 이 꼴이다.
#: 위키 표 머리는 맨 숫자("5080"·"5070 Ti")인 경우가 많다 — 브랜드 없는 쪽도 받는다.
#: **머리 검증은 전체 일치(fullmatch)로 한다** — "117.241 mm 2" 같은 사양 셀이
#: 숫자 대안에 걸려 모델로 둔갑하는 것을 막는다(CPU 문서에서 실측). 뒤에 잡음이
#: 붙은 셀("5080 노트북")은 모델 열로 인정하지 않는다 — 데스크톱 열이 따로 있다.
_MODEL_FULL = re.compile(
    r"(?:(?:NVIDIA\s+)?(?:GeForce\s+)?(?:RTX\s+\d{3,4}(?:\s+(?:Ti|Super))?|GTX\s+\d{3,4}(?:\s+Ti)?)|"
    r"Radeon\s+RX\s+\d{3,4}(?:\s+(?:XT|XTX))?|Arc\s+[AB]\d{3}|H\d{3}|A\d{2,3}|L40S?|RTX\s+PRO\s+\d+|"
    r"Core\s+(?:Ultra\s+)?[i3579]\d?(?:[ -]\d{4,5}[A-Za-z]*)?|Xeon\s+[A-Za-z0-9-]+|"
    r"Ryzen\s+\d\s*\d{3,4}X?(?:\s+X3D)?|EPYC\s+\d{3,4}[A-Za-z]*|"
    r"Pentium|Celeron|Athlon|Threadripper\s+\d{4,5})",
    re.IGNORECASE,
)

#: 맨 숫자 모델의 세대 규칙 — GPU 세대 번호(10·16·20·30·40·50·60 / Radeon 76~79·90)에
#: 세대 내 등급(00·50·60·70·80·90)만 받는다. "1008"(대역폭)·"1024"·"4096"(SP 수) 같은
#: 사양 값이 모델로 둔갑하는 것을 차단한다(실측: RX 9000 문서의 SP 표).
_BARE_MODEL = re.compile(r"(?:10|16|20|30|40|50|60|76|77|78|79|90)(?:00|50|60|70|80|90)")

#: 맨 숫자 모델에 붙는 등급 접미 — "5070 Ti"·"9070 XT"·"4080 Super".
_BARE_SUFFIX = re.compile(r"^(?P<num>\d{4,5})(?:\s+(?:Ti|Super|XT|XTX|X3D))?$", re.IGNORECASE)


def _is_model_cell(text: str, allow_bare: bool = True) -> bool:
    """머리 셀 하나가 모델명인가 — 전체 일치만 인정한다.

    CPU 문서의 표는 메모리 속도(4400·5600) 같은 맨 숫자 열이 흔해서
    ``allow_bare=False`` 면 브랜드가 붙은 모델명만 받는다.
    """
    cleaned = str(text or "").strip()
    if _MODEL_FULL.fullmatch(cleaned):
        return True
    if not allow_bare:
        return False
    match = _BARE_SUFFIX.fullmatch(cleaned)
    return bool(match and _BARE_MODEL.fullmatch(match.group("num")))

#: 표 머리에서 모델명이 아닌 잡음(시리즈 이름·링크·각주)을 걷어 낸다.
_CELL_NOISE = re.compile(r"\[\[|\]\]|<ref[^>]*>.*?</ref>|<ref[^/]*/>", re.IGNORECASE | re.DOTALL)

#: 사양에서 의미 있는 숫자 토큰. 자리수 2 이상만 취한다 — 1·2 같은 수량은 열쇠가 못 된다.
#: "16GB"·"400W" 처럼 숫자가 글자에 붙어도 잡아야 한다 — 위키 표는 "16 GB", 규격서는 "16GB"로 쓴다.
_NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?(?![\d,])")

#: 표 블록의 시작·행·셀 마커.
_TABLE_OPEN = "{|"
_TABLE_CLOSE = "|}"


def enabled() -> bool:
    """Wikipedia 탐색을 켤지. 기본 켜짐 — 공개 API 라 설정이 따로 없다."""
    return os.getenv("WIKI_DISCOVERY", "1") != "0"


# ── 숫자 정규화 ──────────────────────────────────────────────────────────────
def spec_numbers(text: str) -> list[str]:
    """사양 문자열에서 숫자 토큰을 뽑는다. 콤마·공백 정규화 — "10,752" 와 "10752" 가 같아진다."""
    out: list[str] = []
    for match in _NUMBER.findall(str(text or "")):
        value = match.replace(",", "")
        if "." in value:
            continue                       # 소수는 표 매칭 노이즈 — 버린다
        value = value.lstrip("0") or "0"
        if len(value) >= 2 and value not in out:
            out.append(value)
    return out


def _norm_numbers(text: str) -> str:
    """숫자 사이 콤마를 지우고 토큰 경계를 살려 매칭용 정규본을 만든다."""
    return re.sub(r"(?<=\d),(?=\d)", "", str(text or ""))


def _cell_text(cell: str) -> str:
    """표 셀 하나에서 모델명·숫자만 남긴다. 링크·각주·HTML·템플릿·속성은 걷어 낸다."""
    cell = _CELL_NOISE.sub(" ", cell)
    cell = re.sub(r"\{\{[^{}]*\}\}", " ", cell)          # {{0}} {{ya}} 등 템플릿
    cell = re.sub(r"<br\s*/?>|</?sup>|</?sub>", " ", cell, flags=re.IGNORECASE)
    cell = re.sub(r"<[^>]+>", " ", cell)                 # 그 밖 HTML 태그
    cell = re.sub(r"(?:colspan|rowspan|style|scope)\s*=\s*\"?[^\s\"]*\"?", " ", cell, flags=re.IGNORECASE)
    cell = re.sub(r"&nbsp;", " ", cell)
    return re.sub(r"\s+", " ", cell).strip()


def _expand(cells: list[str]) -> list[str]:
    """colspan 만큼 셀을 되풀이해 열 위치를 맞춘다. colspan 이 없으면 그대로 한 열."""
    expanded: list[str] = []
    for cell in cells:
        span = 1
        match = re.search(r'colspan\s*=\s*"?(\d+)"?', cell, re.IGNORECASE)
        if match:
            span = max(1, int(match.group(1)))
        expanded.extend([cell] * span)
    return expanded


def _row_cells(line: str) -> list[str]:
    """위키 표 줄 하나(마커 제외한 원문)에서 셀 목록을 뽑는다.

    표 안에서 셀은 `||`·`!!`·줄 시작의 `|`·`!` 로 갈린다. 이 표는 모델 열이
    `! 5050` 꼴 머리와 `| 10,752` 꼴 본문으로 돼 있다 — 둘 다 받는다.
    """
    text = line.strip()
    text = re.sub(r"^[!|]\s?", "", text)
    if "||" in text or "!!" in text:
        return re.split(r"\|\||!!", text)
    return [text]


def parse_model_tables(wikitext: str, allow_bare: bool = True) -> dict[str, str]:
    """위키텍스트에서 '모델명 열 머리를 가진 표'를 찾아 {모델명: 사양 문자열} 을 만든다.

    규칙: 머리 행에서 모델명 셀이 있는 표만 취하고, 이후 각 행의 같은 열 값을
    그 모델의 사양으로 모은다. colspan 은 열 수를 맞추는 데만 쓴다.

    ``allow_bare`` — 맨 숫자 모델 열("5080")을 받을지. GPU 문서는 켜고, CPU 문서는
    끈다(메모리 속도 열이 맨 숫자라 모델로 둔갑한다).

    **행은 `|-` 로만 갈린다** — 위키 표에서 한 행의 셀은 여러 줄에 흩어져 있는 게
    보통이라(머리 행이 대표적), 줄 하나를 행 하나로 읽으면 머리가 전부 깨진다.
    """
    lines = str(wikitext or "").split("\n")
    out: dict[str, str] = {}
    in_table = False
    depth = 0
    header: list[str | None] = []
    column_count = 0
    specs: dict[int, list[str]] = {}
    row_cells: list[str] = []

    def commit_row() -> None:
        nonlocal header, column_count
        if not row_cells:
            return
        if not header:
            # 머리 후보 행 — 셀의 과반이 모델명이어야 표로 인정한다.
            # (사양 데이터 행이 먼저 오는 표가 있어, 숫자 셀 몇 개만으론 인정하지 않는다.)
            expanded = _expand(row_cells)
            labeled = [i for i, c in enumerate(expanded) if _is_model_cell(_cell_text(c), allow_bare)]
            if len(labeled) < 2 or len(labeled) * 2 < len(expanded):
                return
            header = [_cell_text(c) if i in labeled else None for i, c in enumerate(expanded)]
            column_count = len(header)
            return
        expanded = _expand(row_cells)
        for column in range(column_count):
            if column >= len(expanded) or header[column] is None:
                continue
            value = _cell_text(expanded[column])
            if value:
                specs.setdefault(column, []).append(value)

    def finish_table() -> None:
        nonlocal out
        commit_row()
        for column, cells in specs.items():
            if column < len(header) and header[column]:
                out[header[column]] = " ".join(cells)

    for raw in lines:
        line = raw.strip()
        if line.startswith(_TABLE_OPEN):
            if not in_table:
                in_table = True
                depth = 0
                header, column_count, specs, row_cells = [], 0, {}, []
            depth += 1
            continue
        if not in_table:
            continue
        if line.startswith(_TABLE_CLOSE):
            depth -= 1
            if depth <= 0:
                finish_table()
                in_table = False
                header, column_count, specs, row_cells = [], 0, {}, []
            continue
        if line.startswith("|-"):
            commit_row()
            row_cells = []
            continue
        if line.startswith("|") or line.startswith("!"):
            row_cells.extend(_row_cells(line))

    return out


def _score_model(spec_nums: list[str], spec_text: str) -> int:
    """사양 숫자가 모델 사양 문자열에 몇 개나 단어 경계로 있는가."""
    if not spec_nums:
        return 0
    normalized = _norm_numbers(spec_text)
    score = 0
    for value in spec_nums:
        if re.search(rf"(?<![\d.]){re.escape(value)}(?![\d.])", normalized):
            score += 2 if len(value) >= 4 else 1    # 큰 숫자(코어 수 등)는 신뢰도가 높다
    return score


def pick_model(spec_text: str, models: dict[str, str], min_score: int = 3) -> dict:
    """사양 문자열을 모델별 사양과 대조해 최고 득표 모델을 고른다.

    반환은 discover 계약의 발견 결과 꼴({model, modelVotes, candidates}) 이다.
    """
    nums = spec_numbers(spec_text)
    if not nums or not models:
        return {"model": None, "modelVotes": 0, "candidates": []}
    scored: list[tuple[int, str]] = []
    for model, text in models.items():
        score = _score_model(nums, text)
        if score > 0:
            scored.append((score, model))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    candidates = [{"title": model, "url": "", "model": model, "score": score}
                  for score, model in scored[:8]]
    best = scored[0] if scored else (0, "")
    if best[0] < min_score:
        return {"model": None, "modelVotes": 0, "candidates": candidates}
    return {"model": best[1], "modelVotes": best[0], "candidates": candidates}


# ── 네트워크 조회 ─────────────────────────────────────────────────────────────
def _search_query(spec_text: str, category: str) -> str:
    query = re.sub(r"\s+", " ", str(spec_text or "")).strip()[:200]
    return f"{query} {category}".strip()


#: 위키피디아 API 는 연락처가 담긴 User-Agent 를 요구한다(봇 정책). 연락처 없는 UA 는
#: 순식간에 429 로 잘린다(실측) — 반드시 연락처를 넣는다. HTTP 헤더라 **ASCII 만**.
USER_AGENT = "g2bmaster-ai/1.0 (G2B procurement price research; contact: dev@electerior.com)"


async def _api_get(params: dict, lang: str = "en", deadline_s: float = TIMEOUT_S) -> dict:
    async with httpx.AsyncClient(timeout=deadline_s, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(_API.format(lang=lang), params=params)
        response.raise_for_status()
        return response.json()


async def _search_titles(query: str, lang: str, deadline_s: float) -> list[str]:
    payload = await _api_get({
        "action": "query", "list": "search", "srsearch": query,
        "format": "json", "srlimit": _MAX_RESULTS, "srnamespace": "0",
    }, lang=lang, deadline_s=deadline_s)
    return [r.get("title") or "" for r in (payload.get("query", {}).get("search") or [])]


async def _article_wikitext(title: str, lang: str, deadline_s: float) -> str:
    payload = await _api_get({
        "action": "parse", "page": title, "prop": "wikitext",
        "format": "json", "redirects": "1",
    }, lang=lang, deadline_s=deadline_s)
    return (payload.get("parse", {}).get("wikitext", {}) or {}).get("*", "")


async def discover_model(spec_text: str, category: str = "",
                         deadline_s: float = TIMEOUT_S) -> dict[str, Any]:
    """사양 문자열 → 위키 시리즈 문서의 사양표에서 모델명을 특정한다.

    오류는 전부 잡아 not-found 로 흘린다 — 상위가 다음 소스를 시도한다.
    """
    query = _search_query(spec_text, category)
    if not query.strip():
        return {"query": query, "model": None, "candidates": [], "shopLinks": [],
                "status": "not-found", "source": "wikipedia"}
    # CPU 문서는 맨 숫자 열(메모리 속도)이 흔해서 브랜드 붙은 모델명만 받는다.
    allow_bare = str(category or "").strip().upper() != "CPU"
    try:
        for lang in ("en", "ko"):
            titles = await _search_titles(query, lang, deadline_s)
            for title in titles:
                wikitext = await _article_wikitext(title, lang, deadline_s)
                models = parse_model_tables(wikitext, allow_bare=allow_bare)
                picked = pick_model(spec_text, models)
                if picked["model"]:
                    return {
                        "query": query, "model": picked["model"],
                        "modelVotes": picked["modelVotes"],
                        "candidates": picked["candidates"], "shopLinks": [],
                        "status": "found", "source": "wikipedia",
                    }
    except (httpx.HTTPError, AiFailure, ValueError, KeyError):
        pass                                  # 위키가 안 되면 not-found — 치명적이지 않다
    return {"query": query, "model": None, "candidates": [], "shopLinks": [],
            "status": "not-found", "source": "wikipedia"}
