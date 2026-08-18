#!/usr/bin/env python3
"""하드웨어 지식베이스(RAG 코퍼스) 구축 스크립트.

모은다:
  1. Wikipedia GPU·CPU 시리즈 문서의 모델별 사양표(영어 우선, 한국어 보조)
  2. ITMAYA GPU서버 가격표의 기종명(로컬 xlsx)

내보낸다:
  - data/hardware-kb.jsonl  — {category, model, text} 한 줄 한 건
  - data/hardware-kb-vectors.json — ML 스택이 있으면 모델·텍스트 임베딩(선택)

네트워크가 없으면 위키 수집은 건너뛰고 ITMAYA 만으로 만든다. 실행은 멱등 —
다시 돌리면 파일을 통째로 다시 쓴다.

    .venv/bin/python scripts/build_hardware_kb.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import wiki                      # noqa: E402
from app.itmaya import load_index         # noqa: E402
from app.hardware_kb import KB_PATH, VECTORS_PATH  # noqa: E402

#: (문서 제목, 모델명 접두, 카테고리). 머리 셀이 맨 숫자면 접두를 붙여 완전한 모델명으로 만든다.
ARTICLES: list[tuple[str, str, str]] = [
    ("GeForce RTX 50 series", "RTX", "GPU"),
    ("GeForce RTX 40 series", "RTX", "GPU"),
    ("GeForce RTX 30 series", "RTX", "GPU"),
    ("GeForce RTX 20 series", "RTX", "GPU"),
    ("Radeon RX 9000 series", "RX", "GPU"),
    ("Radeon RX 7000 series", "RX", "GPU"),
    ("Radeon RX 6000 series", "RX", "GPU"),
    ("Arrow Lake (microprocessor)", "Core Ultra", "CPU"),
    ("Raptor Lake", "Core i", "CPU"),
    ("Alder Lake", "Core i", "CPU"),
    ("Zen 5", "Ryzen", "CPU"),
    ("Zen 4", "Ryzen", "CPU"),
    ("EPYC 9005 series", "EPYC", "CPU"),
]


def _is_bare_number(text: str) -> bool:
    return bool(str(text).strip() and not str(text).strip()[0].isalpha())


def _full_model(article: str, cell: str, prefix: str) -> str:
    cell = str(cell or "").strip()
    if not cell:
        return ""
    if cell.upper().startswith(prefix.upper()):
        return cell
    if _is_bare_number(cell):
        return f"{prefix} {cell}"
    return cell


async def collect_wikipedia() -> list[dict]:
    rows: list[dict] = []
    for article, prefix, category in ARTICLES:
        for lang in ("en", "ko"):
            try:
                wikitext = await wiki._article_wikitext(article, lang, 12.0)
            except Exception:  # noqa: BLE001 — 한 문서 실패가 전체 실패가 아니다
                wikitext = ""
            if not wikitext:
                # 429 등 일시 차단은 한 번 기다렸다 다시 시도한다(위키 API 는 관대하나 순간 폭주에 걸린다).
                await asyncio.sleep(2.0)
                try:
                    wikitext = await wiki._article_wikitext(article, lang, 12.0)
                except Exception:  # noqa: BLE001
                    wikitext = ""
            await asyncio.sleep(0.5)      # 위키 API 예의상 간격
            if not wikitext:
                continue
            tables = wiki.parse_model_tables(wikitext, allow_bare=category != "CPU")
            if not tables:
                continue
            for cell, spec_text in tables.items():
                model = _full_model(article, cell, prefix)
                if not model:
                    continue
                rows.append({"category": category, "model": model, "text": spec_text})
            break                       # 영어에서 나오면 한국어는 넘어간다
        else:
            print(f"  ! 문서 없음/표 없음: {article}", file=sys.stderr)
    return rows


def collect_itmaya() -> list[dict]:
    """ITMAYA 색인에서 기종명을 코퍼스로. 카테고리 System·GPU 만 — 서버 기종 식별용."""
    index = load_index()
    if not index:
        return []
    rows: list[dict] = []
    by_category = index.get("byCategory") or {}
    for category in ("System", "GPU"):
        for row in by_category.get(category, []):
            model = str(row.get("product") or "").strip()
            name = str(row.get("name") or "").strip()
            if not model:
                continue
            rows.append({
                "category": "GPU" if category == "System" else category,
                "model": model,
                "text": f"{model} {name}".strip(),
            })
    return rows


def dedupe(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for row in rows:
        key = (row["category"].lower(), row["model"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def usable(rows: list[dict]) -> list[dict]:
    """사양 문자열에 숫자 토큰이 2개 이상 있는 행만 남긴다.

    머리 검증을 통과해도 표 구조가 어긋난 문서는 사양 문자열이 잡음뿐일 수 있다 —
    숫자가 없다는 것은 대조할 스펙이 없다는 뜻이므로 코퍼스에 넣지 않는다.
    """
    out = []
    for row in rows:
        if len(wiki.spec_numbers(row["text"])) >= 2:
            out.append(row)
    return out


def build_vectors(rows: list[dict]) -> None:
    """ML 스택이 있으면 모델·텍스트 임베딩을 저장한다. 없으면 그냥 건너뛴다."""
    try:
        from module_a import model_registry
    except ImportError:
        print("  임베딩 스택 미설치 — 벡터 파일은 만들지 않는다(스코어링 경로만 씀)")
        return
    try:
        vectors = model_registry.encode([row["text"] for row in rows])
    except model_registry.ModelUnavailable as error:
        print(f"  임베딩 모델 불가: {error} — 벡터 파일은 만들지 않는다")
        return
    payload = {
        "model": model_registry.DEFAULT_MODEL,
        "rows": rows,
        "vectors": [[float(v) for v in vector] for vector in vectors],
    }
    VECTORS_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"  벡터 저장: {VECTORS_PATH} ({len(rows)}건)")


def main() -> None:
    itmaya_rows = collect_itmaya()
    print(f"ITMAYA: {len(itmaya_rows)}건")
    wiki_rows = asyncio.run(collect_wikipedia())
    print(f"Wikipedia: {len(wiki_rows)}건")
    # ITMAYA 는 큐레이션된 표라 그대로 싣고, 위키만 품질 필터를 태운다.
    rows = dedupe(itmaya_rows + usable(wiki_rows))
    KB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with KB_PATH.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"코퍼스 저장: {KB_PATH} ({len(rows)}건)")
    build_vectors(rows)


if __name__ == "__main__":
    main()
