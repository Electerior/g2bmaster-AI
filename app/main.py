"""g2bmaster-AI — 추론 계층 HTTP 표면.

백엔드(`integration/ai/AiClient`)가 부르는 11개 경로를 그대로 연다.
계약 전문: `g2bmaster-backend/docs/ai-boundary.md`.

**아직 옮기지 않은 경로는 501 `NOT_PORTED` 로 정직하게 응답한다.**
백엔드가 `POST /api/system/backfill` 에 쓰는 것과 같은 규약이다 — "곧 됩니다"를 200 으로
위장하면 폴백 결과가 캐시에 눌러앉아 영원히 재분석되지 않는다(ai-boundary.md §6.3).
무엇이 되고 무엇이 안 되는지는 `PORTING_STATUS.md` 에 적는다.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import PORT, SERVICE_SECRET, get_ai_config, llm_headers, mask_config
from .embedding import EmbeddingUnavailable, embed_texts
from .llm.client import available_models, host, llm_status
from .llm.worker_pool import get_llm_worker_pool
from .prompts import ITEM_SUMMARY_PROMPT_VERSION

logger = logging.getLogger("g2bmaster-ai")

app = FastAPI(
    title="G2B Masters AI",
    description="나라장터 입찰정보의 추론 계층 — LLM 분석·임베딩·법령 검토·가격 웹검색",
    version="0.1.0",
)

OPEN_PATHS = {"/health", "/healthz", "/docs", "/openapi.json", "/redoc"}


def not_ported(path: str, reason: str, blocked_by: str = "") -> JSONResponse:
    """아직 이식하지 않은 경로. 백엔드의 501 NOT_PORTED 규약과 같은 모양이다."""
    body = {"code": "NOT_PORTED", "error": f"{path} 는 아직 이식되지 않았습니다.", "reason": reason}
    if blocked_by:
        body["blockedBy"] = blocked_by
    return JSONResponse(body, status_code=501)


@app.middleware("http")
async def service_secret_guard(request: Request, call_next):
    """AI_SERVICE_SECRET(또는 원본과 같은 INTERNAL_SECRET)을 설정하면 백엔드만 호출할 수 있다."""
    if SERVICE_SECRET and request.url.path not in OPEN_PATHS:
        provided = request.headers.get("x-internal-secret") or ""
        authorization = request.headers.get("authorization") or ""
        bearer = authorization[7:] if authorization.lower().startswith("bearer ") else ""
        if provided != SERVICE_SECRET and bearer != SERVICE_SECRET:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


# ── 상태 ─────────────────────────────────────────────────────────────────────
@app.get("/health")
@app.get("/healthz")
async def health():
    return {"ok": True, "status": "ok", "service": "g2bmaster-ai"}


@app.get("/api/ai/config")
async def ai_config():
    """현재 설정(키는 마스킹). 원본 UI 설정 화면이 쓰던 것과 같은 모양이다."""
    return mask_config(get_ai_config())


# ── 계약 §5: 백엔드가 호출하는 11개 ──────────────────────────────────────────

@app.get("/api/ai/prompt-version")
async def prompt_version():
    """분석 재사용 키에 들어가는 프롬프트 버전. 백엔드가 하드코딩하지 않고 여기서 읽어 간다."""
    return {"promptVersion": ITEM_SUMMARY_PROMPT_VERSION}


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
async def embed(payload: dict):
    """텍스트 임베딩. 유사도 계산은 백엔드가 한다."""
    texts = payload.get("texts")
    texts = [str(text) for text in texts] if isinstance(texts, list) else []
    try:
        return embed_texts(texts, str(payload.get("model") or ""))
    except EmbeddingUnavailable as error:
        return JSONResponse({"code": "EMBEDDING_UNAVAILABLE", "error": str(error)}, status_code=503)


@app.post("/api/item-summary")
async def item_summary(payload: dict):
    return not_ported(
        "/api/item-summary",
        "심층 분석은 첨부 원문(Markdown)·문서 신호를 입력으로 받는다. "
        "그 입력을 만드는 백엔드의 첨부 파싱(HWP·PDF·ZIP)이 아직 이식되지 않아 "
        "계약된 입력이 존재하지 않는다.",
        blocked_by="g2bmaster-backend: 첨부 파싱(porting-status.md '첨부 파싱 ❌ 미착수')",
    )


@app.post("/api/bid-summary")
async def bid_summary(payload: dict):
    return not_ported(
        "/api/bid-summary",
        "원본 lib/bid-summary.js 이식 예정. LLM 호출 계층(app/llm/)은 준비됐고 프롬프트 이식이 남았다.",
    )


@app.post("/api/legal/review-clauses")
async def review_clauses(payload: dict):
    return not_ported(
        "/api/legal/review-clauses",
        "원본 lib/legal-review.js + lib/law-mcp.js 이식 예정. korean-law-mcp 는 이 저장소에 들어와 있다.",
    )


@app.post("/api/legal/outreach-draft")
async def outreach_draft(payload: dict):
    return not_ported("/api/legal/outreach-draft", "원본 lib/legal-review.js 이식 예정.")


@app.post("/api/pledge/revision-workflow")
async def pledge_revision(payload: dict):
    return not_ported(
        "/api/pledge/revision-workflow",
        "원본 lib/pledge-workflow.js + lib/pledge-revision.js 이식 예정.",
    )


@app.post("/api/price/resolve")
async def price_resolve(payload: dict):
    return not_ported(
        "/api/price/resolve",
        "원본 price-web.js 이식 예정. studyweb 검색 연동과 LLM 가격 추출이 함께 필요하다.",
    )


@app.post("/api/price/url")
async def price_url(payload: dict):
    return not_ported("/api/price/url", "원본 price-web.js 이식 예정.")


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return JSONResponse({"error": "not found", "path": request.url.path}, status_code=404)
