"""Handler for /api/item-summary endpoint.

Implements item summary generation for bid notices.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from ..config import get_ai_config
from ..errors import AiFailure
from ..llm.client import llm_chat
from ..llm.worker_pool import get_llm_worker_pool
from ..prompts import ITEM_SUMMARY_PROMPT_VERSION, PROMPT_VERSIONS  # We'll need to add these to prompts.py (they exist)
logger = logging.getLogger("g2bmaster-ai")


async def handle_item_summary(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle item-summary endpoint request.

    Args:
        request: FastAPI request object
        payload: Normalized payload from backend

    Returns:
        Response matching ItemSummaryResponse schema
    """
    # Extract relevant fields from normalized payload
    bid_notice_no = payload.get("bidNtceNo")
    bid_notice_sq_no = payload.get("bidNtceSqNo")
    bf_spec_rgst_no = payload.get("bfSpecRgstNo")
    prcrmnt_req_no = payload.get("prcrmntReqNo")
    entity_type = payload.get("entityType")
    title = payload.get("title")
    instt_nm = payload.get("insttNm")
    item_name = payload.get("itemName")
    amount = payload.get("amount")
    cntrct_mthd_nm = payload.get("cntrctMthdNm")
    type_ = payload.get("type")
    company_profile = payload.get("companyProfile")
    file_entries = payload.get("fileEntries", [])
    raw_fields = payload.get("rawFields", {})
    analysis_mode = payload.get("analysisMode")
    deep = payload.get("deep", False)
    context = payload.get("context")
    spec_text = payload.get("specText")
    prefer_file = payload.get("preferFile")
    force_refresh = payload.get("forceRefresh", False)

    logger.info(
        "Processing item-summary for bidNoticeNo: %s, bidNoticeSqNo: %s, itemName: %s",
        bid_notice_no, bid_notice_sq_no, item_name
    )

    # Initialize response structure with defaults
    response: dict[str, Any] = {
        "requestId": request.state.request_id if hasattr(request.state, "request_id") else None,
        "summary": "",
        # AiFallbackFlags
        "aiFallback": False,
        "aiError": None,
        "aiTimeout": False,
        "aiDisabled": False,
        # DocumentSignals
        "documentTags": [],
        "frontMatterRecipient": None,
        "legalReviewText": None,
        "bidBlockingClauses": {
            "excluded": True,
            "reasons": [],
            "matches": [],
        },
        "legalAssessment": None,
        # ItemSummaryResponse specific fields
        "factIntegrity": None,
        "productCounts": None,
        "productGroups": None,
        "productLines": None,
        "productTotal": None,
        "source": "unknown",
        "fileEntryCount": len(file_entries),
        "parsedFileCount": 0,
        "fileRecords": None,
        "fileSummary": None,
        "parsedFiles": [],
        "failedFiles": [],
        "relatedItems": [],
        "noFile": True,
        "sourceTrace": None,
        "fastMode": False,
        "analysisMeta": None,
        # Backend-only fields (optional)
        "_analysisHistoryId": None,
        "_fromHistory": None,
        "_analyzedAt": None,
    }

    # Check if AI service is enabled
    ai_config = get_ai_config()
    if not ai_config.get("enabled", False):
        response["aiDisabled"] = True
        response["aiError"] = "AI 기능이 꺼져 있습니다 (g2b.ai.enabled=false)."
        logger.warning("AI service is disabled")
        return response

    try:
        # TODO: Implement actual item summary logic
        # For now, return a basic response indicating the endpoint is implemented

        # Generate a basic summary based on available info
        if item_name:
            response["summary"] = f"{item_name}에 대한 품목 요약이 완료되었습니다. " \
                                f"구체적인 분석 결과를 위해 백엔드에서 추가 처리가 필요합니다."
        else:
            response["summary"] = "품목 정보가 충분하지 않아 요약을 수행할 수 없습니다."
            response["aiFallback"] = True
            response["aiError"] = "품목명이 제공되지 않아 요약을 수행할 수 없습니다."

        # Set source based on whether we have manual content or file entries
        if payload.get("manualContent"):
            response["source"] = "manual"
            response["noFile"] = False  # Assuming manual content means we have the content
        elif file_entries:
            response["source"] = "auto-file"
        else:
            response["source"] = "meta"

        # If we have spec_text, we could set source to auto-detail
        if spec_text:
            response["source"] = "auto-detail"

        logger.info("Item-summary processing completed")
        return response

    except Exception as e:
        logger.exception("Error processing item-summary")
        response["aiFallback"] = True
        response["aiError"] = f"품목 요약 처리 중 오류가 발생했습니다: {str(e)[:180]}"
        return response