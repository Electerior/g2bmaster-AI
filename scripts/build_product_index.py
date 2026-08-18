#!/usr/bin/env python3
"""다나와 상품 로컬 인덱스 구축 — 카테고리 목록(인기순 30건)을 polite rate 로 긁는다.

    .venv/bin/python scripts/build_product_index.py

경고: **반드시 polite rate**(카테고리당 1초 이상 간격) 로 돌린다.
다나와가 이 IP 를 막으면 가격 파서(매일 배치)까지 함께 죽는다 — 인덱스 몇 줄 때문에
가격 소스를 잃는 트레이드는 없다. 실행은 멱등 — 파일을 통째로 다시 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.product_index import CATEGORIES, INDEX_PATH, crawl_category  # noqa: E402


async def collect() -> list[dict]:
    rows: list[dict] = []
    for cate, label in CATEGORIES:
        got = await crawl_category(cate, label)
        print(f"  {label:6} cate={cate}: {len(got)}건", file=sys.stderr)
        rows.extend(got)
        await asyncio.sleep(2.0)       # polite rate — 카테고리 간 2초
    return rows


def dedupe(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        if row["pcode"] in seen:
            continue
        seen.add(row["pcode"])
        out.append(row)
    return out


async def main() -> None:
    rows = dedupe(await collect())
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"인덱스 저장: {INDEX_PATH} ({len(rows)}건)")


if __name__ == "__main__":
    asyncio.run(main())
