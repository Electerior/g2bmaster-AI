"""`POST /api/item-summary` — 공고·사전규격·발주계획 1건 심층 분석.

**LLM 분석 로직은 아직 옮기지 않았다.** 그래서 이 표면은 나머지 미이식 경로와 같이
`501 NOT_PORTED` 로 응답한다. 200 을 돌려주면 안 되는 이유가 이 경로에서 특히 크다 —
백엔드 `AnalysisJobRunner` 는 `AiClient.itemSummary` 가 예외를 던지지 않은 응답을
성공으로 보고 `analysis_history` 에 적재한 뒤 작업을 완료 처리한다. 재사용 키
(입력해시 + 프롬프트버전)가 같으므로 그 행은 **영원히 재분석되지 않는다.**

이식할 원본은 `lib/analysis-executor.js` 이고 4스텝이다:
clamp → facts → summary → items (+legal). `CLAUDE.md §3` 참고.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from ..errors import AiFailure

logger = logging.getLogger("g2bmaster-ai")


async def handle_item_summary(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """분류된 미이식 실패를 올린다. 본문 조립은 `AiFailure` 핸들러가 한다."""
    logger.info(
        "item-summary 요청 — bidNtceNo=%s itemName=%s (미이식)",
        payload.get("bidNtceNo"),
        payload.get("itemName"),
    )
    raise AiFailure(
        "NOT_PORTED",
        detail="/api/item-summary",
        reason="원본 lib/analysis-executor.js 의 4스텝(clamp → facts → summary → items) 이식 예정.",
        blockedBy="백엔드 첨부 파싱 — documents[].text 가 넘어와야 본 요약을 만들 수 있다.",
    )
