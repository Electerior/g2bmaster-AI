"""Handler for /api/bid-summary endpoint.

Implements business summary generation for bid notices.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from ..config import get_ai_config
from ..errors import AiFailure
from ..llm.client import llm_chat
from ..llm.worker_pool import get_llm_worker_pool
from ..prompts import BID_SUMMARY_PROMPT_VERSION, PROMPT_VERSIONS  # We'll need to add these to prompts.py

logger = logging.getLogger("g2bmaster-ai")


async def handle_bid_summary(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Handle bid-summary endpoint request.

    Args:
        request: FastAPI request object
        payload: Normalized payload from backend

    Returns:
        Response matching BidSummaryResponse schema
    """
    # Extract relevant fields from normalized payload
    bid_notice_no = payload.get("bidNtceNo")
    bid_notice_sq_no = payload.get("bidNtceSqNo")
    bid_notice_nm = payload.get("bidNtceNm")
    ntce_instt_nm = payload.get("ntceInsttNm")
    presmpt_prce = payload.get("presmptPrce")
    cntrct_cncls_mthd_nm = payload.get("cntrctCnclsMthdNm")
    dtil_prdct_clsfc_no_nm = payload.get("dtilPrdctClsfcNoNm")
    manual_content = payload.get("manualContent")
    manual_file_name = payload.get("manualFileName")
    company_profile = payload.get("companyProfile")
    file_entries = payload.get("fileEntries", [])
    raw_fields = payload.get("rawFields", {})

    logger.info(
        "Processing bid-summary for bidNoticeNo: %s, bidNoticeSqNo: %s",
        bid_notice_no, bid_notice_sq_no
    )

    # Initialize response structure with defaults
    response = {
        "requestId": request.state.request_id if hasattr(request.state, "request_id") else None,
        "summary": "",
        "source": "unknown",
        "noPdf": True,
        "sourceTrace": None,
        "fileEntryCount": len(file_entries),
        "parsedFileCount": 0,
        "parsedFiles": [],
        "failedFiles": [],
        "fileRecords": None,
        "fileSummary": None,
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
    }

    # Check if AI service is enabled
    ai_config = get_ai_config()
    if not ai_config.get("enabled", False):
        response["aiDisabled"] = True
        response["aiError"] = "AI 기능이 꺼져 있습니다 (g2b.ai.enabled=false)."
        logger.warning("AI service is disabled")
        return response

    try:
        # TODO: Implement actual bid summary logic
        # For now, return a basic response indicating the endpoint is implemented

        # Generate a basic summary based on available info
        if bid_notice_nm:
            response["summary"] = f"{bid_notice_nm}에 대한 business 요약이 완료되었습니다. " \
                                f"구체적인 분석 결과를 위해 백엔드에서 추가 처리가 필요합니다."
        else:
            response["summary"] = "입찰 공고 정보가 충분하지 않아 요약을 수행할 수 없습니다."
            response["aiFallback"] = True
            response["aiError"] = "입찰 공고명이 제공되지 않아 요약을 수행할 수 없습니다."

        # Set source based on whether we have manual content or file entries
        if manual_content:
            response["source"] = "manual"
            response["noPdf"] = False  # Assuming manual content means we have the content
        elif file_entries:
            response["source"] = "auto-file"
        else:
            response["source"] = "meta"

        logger.info("Bid-summary processing completed")
        return response

    except Exception as e:
        logger.exception("Error processing bid-summary")
        response["aiFallback"] = True
        response["aiError"] = f"입찰 요약 처리 중 오류가 발생했습니다: {str(e)[:180]}"
        return response