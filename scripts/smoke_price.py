#!/usr/bin/env python3
"""가격 조회 연기 테스트 — 실제 소스(다나와·에누리·아이티마야)에 붙는다. 네트워크가 없으면 SKIP.

계약 테스트(test_http_contract.py)는 환경에 흔들리면 안 되므로 소스를 치지 않는다.
실제 조회가 되는지는 여기서 따로 확인한다 — smoke_llm.py 가 실제 LLM 에 붙는 것과 같다.
DoD 에는 포함되지 않는다(네트워크 필요).
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import enuri, itmaya  # noqa: E402
from app.price import resolve, resolve_by_url  # noqa: E402


async def _smoke_danawa() -> bool:
    print("\n[다나와] resolve('RTX 5090')")
    try:
        result = await resolve({"itemName": "RTX 5090", "deadlineMs": 8000})
    except Exception as error:  # noqa: BLE001
        print(f"  SKIP — 소스에 붙지 못했습니다: {type(error).__name__} {error}")
        return True

    info = result["searchInfo"]
    print(f"  status={info['status']} count={info['count']} degraded={result['degraded']}")
    for reason in result["degradedReasons"]:
        print(f"    degraded: {reason['source']} {reason['code']}")
    for q in result["quotes"][:5]:
        price = f"{q['priceKrw']:,}원" if isinstance(q["priceKrw"], int) else "미상"
        print(f"    - [{q['source']}#{q['sourceId']}] {price:>14}  {q['name'][:48]}")

    ok = True
    if info["status"] == "found":
        ok &= all(isinstance(q["priceKrw"], int) and q["priceKrw"] > 0
                  for q in result["quotes"] if q["source"] == "danawa")
        danawa = [q for q in result["quotes"] if q["source"] == "danawa"]
        if danawa:
            first = danawa[0]
            by_url = await resolve_by_url({"url": first["url"]})
            matched = [q for q in by_url["quotes"] if q["sourceId"] == first["sourceId"]]
            print(f"  price/url: {first['url']} → {'일치' if matched else '불일치'}")
            ok &= bool(matched)
    return ok


async def _smoke_enuri() -> bool:
    print("\n[에누리] enuri.search('RTX 5090')")
    try:
        quotes = await enuri.search("RTX 5090", 8000)
    except Exception as error:  # noqa: BLE001
        print(f"  SKIP — 에누리에 붙지 못했거나 파서 재확인 필요: {type(error).__name__} {error}")
        print("  (enuri.py 상단 'VERIFY LIVE' 선택자를 라이브 페이지로 재확인하세요.)")
        return True

    print(f"  count={len(quotes)}")
    for q in quotes[:5]:
        price = f"{q['priceKrw']:,}원" if isinstance(q["priceKrw"], int) else "미상"
        print(f"    - [{q['source']}#{q['sourceId']}] {price:>14}  {q['name'][:48]}")
    ok = all(q["source"] == "enuri" and q["basis"] == "listed" for q in quotes)
    if not quotes:
        print("  (0건 — VERIFY LIVE 선택자 점검 대상일 수 있음)")
    return ok


async def _smoke_itmaya() -> bool:
    # 파일 색인이라 네트워크가 없다 — xlsx 만 있으면 항상 돈다.
    print("\n[아이티마야] itmaya.search('GPU 서버 ESC8000-E12 RTX PRO 6000')")
    quotes = await itmaya.search("GPU 서버 ESC8000-E12 RTX PRO 6000")
    print(f"  count={len(quotes)}")
    for q in quotes[:5]:
        price = f"{q['priceKrw']:,}원" if isinstance(q["priceKrw"], int) else "미상"
        print(f"    - [{q['source']}] {price:>14}  {q['name'][:48]}  ({q['collectedAt']})")
    consumer = await itmaya.search("삼성 노트북 갤럭시북")
    ok = all(q["basis"] == "stale" and q["stale"] for q in quotes) and consumer == []
    return ok


async def main() -> int:
    print("가격 조회 스모크 — 실제 소스")
    results = [await _smoke_danawa(), await _smoke_enuri(), await _smoke_itmaya()]
    ok = all(results)
    print("\nsmoke_price:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
