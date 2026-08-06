"""AI 설정의 단일 소스 — 원본 `lib/ai-config.js` 이식.

우선순위: `data/ai-config.json`(로컬 UI 저장) > 환경변수 > 기본값.
키는 이 로컬 파일에만 두고 브라우저에는 마스킹만 노출한다. 원본과 같은 규칙이다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

FIELDS = [
    "llmBase", "llmApiKey", "llmModel", "llmTemperature", "llmTopP",
    "llmMaxTokens", "llmContextWindow", "searchProvider", "searchUrl",
    "searchKey", "searchPlatforms", "pricePrompt",
]
KEY_FIELDS = {"llmApiKey", "searchKey"}

# 가격 검색 우선 플랫폼 기본값. 원본 주석 그대로: 컴퓨존은 가격을 JS 로 그려
# studyweb 이 "none priced" 로 흘리므로 단독 지정 금지 — URL 수동 경로로 커버한다.
DEFAULT_PLATFORMS = "다나와, 에누리, 컴퓨존, 쿠팡, 11번가, 네이버쇼핑, 숨고, 크몽, 알바몬, 알바천국, 사람인"


def config_dir() -> Path:
    return Path(os.getenv("ATTACHMENT_CACHE_DIR") or (Path(__file__).resolve().parent.parent / "data"))


def config_path() -> Path:
    return config_dir() / "ai-config.json"


def env_defaults() -> dict:
    return {
        "llmBase": os.getenv("LMS_BASE") or "http://localhost:1234",
        "llmApiKey": os.getenv("LLM_API_KEY") or "",
        "llmModel": os.getenv("LMS_MODEL") or "",
        "llmTemperature": os.getenv("LLM_TEMPERATURE") or "0",
        "llmTopP": os.getenv("LLM_TOP_P") or "0.95",
        "llmMaxTokens": os.getenv("LLM_MAX_TOKENS") or "8192",
        "llmContextWindow": os.getenv("LLM_CONTEXT_WINDOW") or "32768",
        "searchProvider": os.getenv("SEARCH_PROVIDER") or "studyweb",
        "searchUrl": os.getenv("STUDYWEB_URL") or "http://localhost:8787",
        "searchKey": os.getenv("STUDYWEB_API_KEY") or "",
        "searchPlatforms": os.getenv("SEARCH_PLATFORMS") or DEFAULT_PLATFORMS,
        "pricePrompt": os.getenv("PRICE_PROMPT") or "",
    }


def read_file() -> dict:
    try:
        loaded = json.loads(config_path().read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve_config(file_values: dict | None = None, env: dict | None = None) -> dict:
    """순수 병합 — 파일 값 중 '비어 있지 않은' 것만 env 위에 덮는다."""
    file_values = file_values or {}
    out = dict(env if env is not None else env_defaults())
    for key in FIELDS:
        value = file_values.get(key)
        if value is not None and str(value).strip() != "":
            out[key] = value
    out["llmBase"] = str(out.get("llmBase") or "").rstrip("/")
    out["searchUrl"] = str(out.get("searchUrl") or "").rstrip("/")
    return out


def get_ai_config() -> dict:
    return resolve_config(read_file(), env_defaults())


def set_ai_config(partial: dict | None = None) -> dict:
    """부분 저장. 키 필드는 빈 값이면 기존 유지(마스크 표시를 덮어쓰지 않도록)."""
    partial = partial or {}
    current = read_file()
    next_values = dict(current)
    for key in FIELDS:
        if key not in partial:
            continue
        value = partial[key]
        if key in KEY_FIELDS and (value is None or str(value).strip() == ""):
            continue
        next_values[key] = "" if value is None else str(value)
    config_dir().mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps(next_values, ensure_ascii=False, indent=2), encoding="utf-8")
    return next_values


def mask_config(cfg: dict) -> dict:
    """브라우저 노출용 — 키는 마스킹하고 설정 여부만 알린다."""
    def mask(value):
        text = str(value or "")
        return ("••••" + text[-4:]) if text else ""

    return {
        "llmBase": cfg.get("llmBase"),
        "llmModel": cfg.get("llmModel"),
        "searchProvider": cfg.get("searchProvider"),
        "searchUrl": cfg.get("searchUrl"),
        "searchPlatforms": cfg.get("searchPlatforms") or "",
        "pricePrompt": cfg.get("pricePrompt") or "",
        "llmApiKey": mask(cfg.get("llmApiKey")),
        "searchKey": mask(cfg.get("searchKey")),
        "llmApiKeySet": bool(cfg.get("llmApiKey")),
        "searchKeySet": bool(cfg.get("searchKey")),
    }


# ── 서비스 자체 설정 ─────────────────────────────────────────────────────────
# 백엔드가 AI_BASE_URL 기본값으로 http://localhost:8000 을 쓴다.
PORT = int(os.getenv("PORT") or 8000)
HOST = os.getenv("HOST") or "127.0.0.1"

# 호출자 인증. 설정하면 Authorization: Bearer 또는 X-Internal-Secret 을 요구한다.
SERVICE_SECRET = os.getenv("AI_SERVICE_SECRET") or os.getenv("INTERNAL_SECRET") or ""


def llm_headers() -> dict:
    """외부 OpenAI 호환 API 용 인증 헤더. 로컬 LM Studio 는 키가 없다."""
    key = get_ai_config().get("llmApiKey")
    return {"Authorization": f"Bearer {key}"} if key else {}
