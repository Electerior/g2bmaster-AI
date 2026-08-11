"""`POST /api/bid-summary` — 공고 요약(영업 관점).

`item-summary` 와 프롬프트가 다르다. **LLM 분석 로직은 아직 옮기지 않았다** —
미이식은 200 이 아니라 `501 NOT_PORTED` 다(`CLAUDE.md §2` 규칙 1).

여기는 `item-summary` 와 달리 작업 큐를 거치지 않지만, 그렇다고 지어낸 요약을
200 으로 내보내면 호출부가 그것을 진짜 요약으로 취급한다 — 화면에 그대로 렌더링된다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from ..errors import AiFailure

logger = logging.getLogger("g2bmaster-ai")


async def handle_bid_summary(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """분류된 미이식 실패를 올린다. 본문 조립은 `AiFailure` 핸들러가 한다."""
    logger.info(
        "bid-summary 요청 — bidNtceNo=%s (미이식)",
        payload.get("bidNtceNo"),
    )
    raise AiFailure(
        "NOT_PORTED",
        detail="/api/bid-summary",
        reason="영업 요약 프롬프트와 LLM 호출 이식 예정. item-summary 와 프롬프트가 다르다.",
    )
