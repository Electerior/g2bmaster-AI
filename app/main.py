"""g2bmaster-AI — 추론 계층 HTTP 표면.

백엔드(`integration/ai/AiClient`)가 부르는 11개 경로를 그대로 연다.
계약 전문: `g2bmaster-backend/docs/ai-boundary.md`.

**아직 옮기지 않은 경로는 501 `NOT_PORTED` 로 정직하게 응답한다.**
"�곧 됩니다"를 200 으로 위장하면 폴백 결과가 캐시에 � 눌러앉아 영원히 재분석되지 않는다
(ai-boundary.md §6.3). 무엇이 되고 무엇이 안 되는지는 `PORTING_STATUS.md` 에 적는다.

**실패는 전부 `app/errors.py` 한 곳으로 모인다.** 예전에는 경로마다 본문 모양이 달라
(`{code,error,reason}` · `{error,path}` · `{error}` · `{detail:[...]}`) 백엔드가
하나의 파서로 읽을 수 없었다. 라우트는 `AiFailure` � 를 올리기만 하고 조립은 � 핸들러가 한다.
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import PORT, SERVICE_SECRET, get_ai_config, llm_headers, mask_config
from .embedding import EmbeddingUnavailable, embed_texts, status as embedding_status
from .errors import AiFailure, resolve_request_id, to_body
from .price import resolve as resolve_price, resolve_by_url as resolve_price_by_url
from .estimate import estimate_unit_cost
from .prebuilt import find_prebuilt_comparables
from .llm.client import available_models, host, llm_status
from .llm.worker_pool import get_llm_worker_pool
from .prompts import ITEM_SUMMARY_PROMPT_VERSION, PROMPT_VERSIONS
from .handlers.item_summary_handler import handle_item_summary
from .handlers.bid_summary_handler import handle_bid_summary

logger = logging.getLogger("g2bmaster-ai")

app = FastAPI(
    title="G2B Masters AI",
    description="나라장터 입찰정보의 추론 계층 — LLM 분석·임베딩·법령 검토·가격 � 웹검색",
    version="0.1.0",
)

OPEN_PATHS = {"/health", "/healthz", "/docs", "/openapi.json", "/redoc"}


def not_ported(request: Request, payload: object, path: str, reason: str, blocked_by: str = "") -> None:
    """아직 이식하지 않은 경로. 요청 ID � 를 본문에서 건져 두고 분류된 실패를 올린다."""
    request.state.request_id = resolve_request_id(request, payload)
    raise AiFailure("NOT_PORTED", detail=path, reason=reason, blockedBy=blocked_by)


# ── 실패 응답 ────────────────────────────────────────────────────────────────
def _fail(request: Request, failure: AiFailure) -> JSONResponse:
    request_id = resolve_request_id(request)
    if failure.code == "INTERNAL":
        # 분류되지 않은 예외 = 우리 버그. 스택을 남긴다.
        logger.exception("unclassified error requestId=%s", request_id)
    else:
        logger.warning("request failed requestId=%s code=%s detail=%s", request_id, failure.code, failure.detail)
    return JSONResponse(to_body(failure, request_id), status_code=failure.spec.status)


@app.exception_handler(AiFailure)
async def ai_failure_handler(request: Request, exc: AiFailure) -> JSONResponse:
    return _fail(request, exc)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI 기본 422 `{detail:[...]}` � 를 덮는다.

    이걸 안 걸면 본문 오타가 `code` 도 `retryable` 도 없는 모양으로 나가고 백엔드는 분류에
    실패한다. 호출자 쪽 문제라 재시도해도 결과가 같다 — `retryable=false` 여야 한다.
    """
    return _fail(request, AiFailure("BAD_REQUEST", detail=str(exc.errors())[:500]))


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """우리가 소유하지 않는 경로(404) 등. 4xx � 는 호출자 문제이므로 재시도 대상이 아니다."""
    code = "BAD_REQUEST" if exc.status_code < 500 else "INTERNAL"
    return _fail(request, AiFailure(code, detail=f"{request.method} {request.url.path}"))


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    return _fail(request, AiFailure("INTERNAL", detail=str(exc)[:500]))


@app.middleware("http")
async def service_secret_guard(request: Request, call_next):
    """AI_SERVICE_SECRET(또는 원본과 같은 INTERNAL_SECRET)을 설정하면 백엔드만 호출할 수 있다.

    요청 ID 도 여기서 먼저 잡는다 — 라우트가 본문에서 더 나은 값을 찾으면 덮어�쓴다.
    """
    request.state.request_id = request.headers.get("x-request-id") or str(uuid.uuid4())

    if SERVICE_SECRET and request.url.path not in OPEN_PATHS:
        provided = request.headers.get("x-internal-secret") or ""
        authorization = request.headers.get("authorization") or ""
        bearer = authorization[7:] if authorization.lower().startswith("bearer ") else ""
        if provided != SERVICE_SECRET and bearer != SERVICE_SECRET:
            return _fail(request, AiFailure("UNAUTHORIZED", detail=request.url.path))
    return await call_next(request)


@app.on_event("startup")
async def _warm_embedding_model() -> None:
    """임베딩 가중치를 미리 읽어 둔다.

    예열하지 않으면 첫 `/api/embed` 가 모델 적재(실측 약 4초)를 혼자 뒤집어쓴다.
    백그라운드로 돌리는 이유는 기동을 막지 않기 위해서다 — 그동안에도 헬스체크는
    응답해야 하고, ML 스택이 설치돼 있지 않은 배포에서도 서비스는 떠야 한다.
    """
    if os.getenv("EMBEDDING_WARMUP", "1") != "1":
        logger.info("임베딩 예열 건너뜀 (EMBEDDING_WARMUP=0)")
        return
    try:
        from module_a.model_registry import warmup_async

        warmup_async()
    except ImportError:
        logger.info("임베딩 스택이 없어 예열을 건너뜁니다 — /api/embed 는 503 입니다")


# ── 상태 ─────────────────────────────────────────────────────────────────────
@app.get("/health")
@app.get("/healthz")
async def health():
    # 임베딩 상태를 함께 싣는다. 프로세스가 살아 있는 것과 임베딩이 쓸 수 있는 것은
    # 다른 질문이라, 모델을 못 읽어도 ok 는 true 다 — 나머지 표면은 정상으로 돈다.
    return {"ok": True, "status": "ok", "service": "g2bmaster-ai", "embedding": embedding_status()}


@app.get("/api/ai/config")
async def ai_config():
    """현재 설정(키는 마스�킹). 원본 UI 설정 화면이 쓰던 것과 같은 모양이다."""
    return mask_config(get_ai_config())


@app.get("/api/ai/prompt-version")
async def prompt_version():
    """분석 재사용 키에 들어가는 프�롬프트 버전. 백엔드가 하드코딩하지 않고 여기서 읽어 간다.

    `versions` � 를 함께 � 낸다 — 프�롬프트가 **엔드포인트 × 문서종류**로 갈리므로 문자열 하나로는
    표현되지 않는다(`decisions.md F-3`). 기존 `promptVersion` 은 그대로 두므로
    `AiClient.promptVersion()`(그 키만 읽는다)은 영향받지 않는다.
    """
    return {"promptVersion": ITEM_SUMMARY_PROMPT_VERSION, "versions": PROMPT_VERSIONS}


@app.get("/api/ai/capacity")
async def capacity():
    """LLM 워커 동시 처리 용량. 내보내기 작업 ETA 계산에 쓰인다.

    건강 확인을 먼저 돌려 죽은 워커의 용량이 ETA 에 섞이지 않게 한다.
    """
    pool = get_llm_worker_pool(host())
    workers = await pool.health_check(headers=llm_headers())
    return {"capacity": pool.total_capacity, "workers": workers}


@app.get("/api/llm/models")
async def llm_models():
    """모델 목록·도달 여부. 시스템 화면의 상태 표시용."""
    models = await available_models()
    status = await llm_status()
    return {
        "ok": models["ok"],
        "source": models["source"],
        "models": models["models"],
        "reachable": status["reachable"],
        "base": status["base"],
        "loadedModel": status["loadedModel"],
        "availableCount": status["availableCount"],
        "workers": status["workers"],
    }


@app.post("/api/embed")
async def embed(request: Request, payload: dict):
    """�텍스트 임베딩. 유사도 계산은 백엔드가 한다."""
    request.state.request_id = resolve_request_id(request, payload)
    texts = payload.get("texts")
    texts = [str(text) for text in texts] if isinstance(texts, list) else []
    try:
        return embed_texts(texts, str(payload.get("model") or ""))
    except EmbeddingUnavailable as error:
        raise AiFailure("EMBEDDING_UNAVAILABLE", detail=str(error)) from error


@app.post("/api/item-summary")
async def item_summary(request: Request, payload: dict):
    request.state.request_id = resolve_request_id(request, payload)
    return await handle_item_summary(request, payload)



@app.post("/api/bid-summary")
async def bid_summary(request: Request, payload: dict):
    request.state.request_id = resolve_request_id(request, payload)
    return await handle_bid_summary(request, payload)
@app.post("/api/legal/review-clauses")
async def review_clauses(request: Request, payload: dict):
    not_ported(
        request,
        payload,
        "/api/legal/review-clauses",
        "원본 lib/legal-review.js + lib/law-mcp.js 이식 예정. korean-law-mcp � 는 이 저장소에 들어와 있다.",
    )


@app.post("/api/legal/outreach-draft")
async def outreach_draft(request: Request, payload: dict):
    not_ported(request, payload, "/api/legal/outreach-draft", "원본 lib/legal-review.js 이식 예정.")


@app.post("/api/pledge/revision-workflow")
async def pledge_revision(request: Request, payload: dict):
    not_ported(
        request,
        payload,
        "/api/pledge/revision-workflow",
        "원본 lib/pledge-workflow.js + lib/pledge-revision.js 이식 예정.",
    )


@app.post("/api/price/resolve")
async def price_resolve(request: Request, payload: dict):
    """품목명 → 가격 후보(다나와·에누리·아이티마야 동시 조회). 검증·선택은 백엔드 소유(§4).

    한 소스가 실패하고 다른 소스가 성공하면 degraded:true + degradedReasons 로 보고한다.
    켜진 소스는 config.PRICE_SOURCES 로 정한다.
    """
    request.state.request_id = resolve_request_id(request, payload)
    return await resolve_price(payload)


@app.post("/api/price/url")
async def price_url(request: Request, payload: dict):
    """상품 URL 하나 → 가격. 다나와 pcode·에누리 modelno 만 화이트리스트, 그 밖은 UNSUPPORTED_SOURCE(§4.5)."""
    request.state.request_id = resolve_request_id(request, payload)
    return await resolve_price_by_url(payload)


@app.post("/api/estimate-unit-cost")
async def estimate_cost(request: Request, payload: dict):
    """규격서 텍스트 → 부품 추출(LLM) → 부품별 가격(다나와) → 원가 추정.

    deal-analysis 의 estimatedUnitCost 갈래(백엔드가 호출). 계약: decisions.md D-P1.
    못 찾으면 에러가 아니라 {matched:false, reason} 200 이다 — 규격서에 부품이 없을 수 있다.
    """
    request.state.request_id = resolve_request_id(request, payload)
    return await estimate_unit_cost(payload)


@app.post("/api/prebuilt-comparables")
async def prebuilt_comparables(request: Request, payload: dict):
    """번들이 완제품인지 부품 조달인지 판정하고, 완제품이면 유사 완제품(다나와)을 찾는다.

    부품 단가 추정과 반대 방향이다 — 완제품 PC 만 남긴다. 계약: docs/api-contract §F.
    """
    request.state.request_id = resolve_request_id(request, payload)
    return await find_prebuilt_comparables(payload)