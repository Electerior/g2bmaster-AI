#!/usr/bin/env python3
"""라이브 LLM 스모크 — 실제로 붙는지 확인한다.

LM Studio(또는 OpenAI 호환 서버)가 떠 있으면 모델 목록·용량·채팅 한 번을 실제로 돌린다.
없으면 SKIP 한다. 계약 검증(`test_http_contract.py`)과 달리 이건 환경에 의존하는 확인이다.

    make smoke                 # 기본 LMS_BASE
    LMS_BASE=http://gpu:1234 make smoke
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.llm.client import available_models, host, llm_status, lms_chat  # noqa: E402
from app.llm.worker_pool import get_llm_worker_pool  # noqa: E402


async def main() -> int:
    status = await llm_status()
    print(f"base       : {host()}")
    print(f"reachable  : {status['reachable']}")

    models = await available_models()
    print(f"source     : {models['source']}  (ok={models['ok']})")
    print(f"models     : {len(models['models'])}개")
    for model in models["models"][:5]:
        print(f"  - {model['id']}  state={model['state']}")

    pool = get_llm_worker_pool(host())
    print(f"capacity   : {pool.total_capacity}")
    for worker in pool.status():
        print(f"  {worker['id']} {worker['base']} healthy={worker['healthy']} model={worker['model']}")

    if not status["reachable"]:
        print("\nsmoke_llm: SKIP — 도달 가능한 LLM 워커가 없습니다.")
        return 0

    loaded = status["loadedModel"]
    if not loaded:
        print("\nsmoke_llm: SKIP — 로드된 채팅 모델이 없습니다(LM Studio 에서 모델을 로드하세요).")
        return 0

    print(f"\n채팅 호출: {loaded}")
    try:
        data = await lms_chat({
            "model": loaded,
            "max_tokens": 32,
            "temperature": 0,
            "messages": [{"role": "user", "content": "한 문장으로 답하세요: 나라장터는 무엇입니까?"}],
        })
    except httpx.HTTPStatusError as error:
        # 모델이 설치만 되고 로드되지 않은 상태가 흔하다. 서버가 알려 준 사유를 그대로 보여 준다.
        print(f"\nsmoke_llm: SKIP — {error}")
        print("  (LM Studio 개발자 페이지에서 모델을 로드하거나 `lms load <model>` 을 실행하세요.)")
        return 0

    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    print(f"응답: {content.strip()[:200]}")
    if not content.strip():
        print("smoke_llm: FAIL — 빈 응답", file=sys.stderr)
        return 1

    print("smoke_llm: OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
