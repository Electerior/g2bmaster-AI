"""`POST /api/notice-summary` — 공고 1건을 사람이 읽을 요약 하나로.

`item-summary`·`bid-summary` 두 표면을 대체한다. 원본 모놀리스의 4스텝
(clamp → facts → summary → items)을 **한 번의 LLM 호출**로 줄인 것이다 —
가격 추정과 품목 추출을 폐기했으므로 그 단계들이 존재할 이유가 없다.

**실패를 200 으로 포장하지 않는다**(`CLAUDE.md §2` 규칙 1). 과거 이 두 표면은
"…요약이 완료되었습니다" 라는 지어낸 문장을 `aiFallback=false` 로 실어 보냈고,
`AnalysisJobRunner` 가 그것을 성공으로 적재해 그 행은 영원히 재분석되지 않았다
(`PORTING_STATUS.md:98-109`). 200 폴백은 백엔드 컨트롤러가 만든다.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import Request

from ..config import get_ai_config
from ..errors import AiFailure
from ..llm.client import lms_chat, loaded_context, loaded_model
from ..prompts import NOTICE_SUMMARY_PROMPT, NOTICE_SUMMARY_PROMPT_VERSION

logger = logging.getLogger("g2bmaster-ai")

#: 출력 상한. 데드라인 부등식(`CLAUDE.md §2` 규칙 7)을 지키려면 출력이 짧아야 한다 —
#: CPU 추론에서 이 값이 곧 지연이다. "단순 요약"의 정의이기도 하다.
MAX_OUTPUT_TOKENS = 400

#: 컨텍스트를 토큰으로 세지 않고 문자로 어림한다. 한국어는 대략 1토큰 ≈ 1.5자.
#: 출력분과 프롬프트 오버헤드를 뺀 나머지를 입력에 준다.
CHARS_PER_TOKEN = 1.5
PROMPT_OVERHEAD_TOKENS = 512

#: `loaded_context()` 도 설정도 못 읽었을 때의 보수적 하한.
FALLBACK_CONTEXT_TOKENS = 8192


async def _input_budget_chars() -> int:
    """입력에 허용할 문자 수.

    `loaded_context()` 는 **지금 로드된** 컨텍스트를 준다(모델 최대치가 아니다).
    LM Studio 가 `/api/v0/models` 로 알려 주는 값이며, 못 읽으면 0 을 돌려준다 —
    그때는 설정값 `llmContextWindow` 로, 그것도 없으면 하한으로 떨어진다.
    """
    window = await loaded_context()
    if not window:
        try:
            window = int(get_ai_config().get("llmContextWindow") or 0)
        except (TypeError, ValueError):
            window = 0
    window = window or FALLBACK_CONTEXT_TOKENS
    usable = window - MAX_OUTPUT_TOKENS - PROMPT_OVERHEAD_TOKENS
    return max(1000, int(usable * CHARS_PER_TOKEN))


def _render_input(payload: dict[str, Any], budget_chars: int) -> str:
    """공고 메타 + 첨부 본문을 프롬프트 하나로 편다.

    **원문을 변형하지 않는다**(`CLAUDE.md §2` 규칙 6). 자를 뿐이고, 자른 사실은
    표시한다 — 조용히 자르면 요약이 왜 얕은지 아무도 알 수 없다.
    """
    head = [
        f"공고번호: {payload.get('bidNtceNo') or '-'}",
        f"공고명: {payload.get('title') or '-'}",
        f"수요기관: {payload.get('agency') or '-'}",
    ]
    amount = payload.get("amount")
    if amount not in (None, ""):
        head.append(f"금액: {amount}")

    documents = payload.get("documents")
    documents = documents if isinstance(documents, list) else []

    parts = ["\n".join(head)]
    remaining = budget_chars - len(parts[0])
    for document in documents:
        if not isinstance(document, dict):
            continue
        name = str(document.get("name") or "첨부")
        text = str(document.get("text") or "")
        if remaining <= 0:
            parts.append(f"\n[{name}] (분량 초과로 생략)")
            continue
        clipped = text[:remaining]
        suffix = "\n…(이하 생략)" if len(text) > remaining else ""
        parts.append(f"\n[{name}]\n{clipped}{suffix}")
        remaining -= len(clipped)
    return "\n".join(parts)


async def handle_notice_summary(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """공고 1건 → 요약 1건. 실패는 분류된 오류로 올린다."""
    if not isinstance(payload, dict) or not (payload.get("title") or payload.get("bidNtceNo")):
        raise AiFailure("BAD_REQUEST", detail="title 과 bidNtceNo 가 모두 비어 있다")

    budget = await _input_budget_chars()
    text = _render_input(payload, budget)
    model = await loaded_model()

    try:
        response = await lms_chat({
            "model": model,
            "messages": [
                {"role": "system", "content": NOTICE_SUMMARY_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": MAX_OUTPUT_TOKENS,
        })
    except httpx.TimeoutException as error:
        raise AiFailure("LLM_TIMEOUT", detail=str(error)[:200]) from error
    except httpx.HTTPError as error:
        raise AiFailure("LLM_UNAVAILABLE", detail=str(error)[:200]) from error

    try:
        summary = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise AiFailure("LLM_MALFORMED", detail=str(response)[:200]) from error

    summary = (summary or "").strip()
    if not summary:
        # 빈 문자열을 200 으로 내보내면 호출부가 "요약이 없는 공고"로 오해한다.
        # 사고 이력(§모듈 docstring)과 같은 종류의 조용한 실패다.
        raise AiFailure("LLM_MALFORMED", detail="빈 요약")

    logger.info(
        "notice-summary 완료 bidNtceNo=%s 입력=%d자 출력=%d자",
        payload.get("bidNtceNo"), len(text), len(summary),
    )
    return {
        "summary": summary,
        "promptVersion": NOTICE_SUMMARY_PROMPT_VERSION,
        "llmModel": model,
    }
