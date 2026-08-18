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
    "searchKey", "searchPlatforms", "pricePrompt", "priceSources",
]
KEY_FIELDS = {"llmApiKey", "searchKey"}

# 가격 검색 우선 플랫폼 기본값. 원본 주석 그대로: 컴퓨존은 가격을 JS 로 그려
# studyweb 이 "none priced" 로 흘리므로 단독 지정 금지 — URL 수동 경로로 커버한다.
DEFAULT_PLATFORMS = "다나와, 에누리, 컴퓨존, 쿠팡, 11번가, 네이버쇼핑, 숨고, 크몽, 알바몬, 알바천국, 사람인"

# 멀티소스 가격 리졸버(price.resolve)가 동시에 칠 소스. 직접 조회형 소스만 나열한다.
# itmaya 는 제거됐다(2026-08-14) — stale GPU서버 가격표 행이 웹 견적을 오염시켰다.
# DEFAULT_PLATFORMS 와 별개다 — 이쪽은 실제로 파서가 붙어 있는 소스만 나열한다.
DEFAULT_PRICE_SOURCES = "danawa,enuri"


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
        # studyweb 은 전용 스크래퍼로 대체돼 사라졌다. 이 슬롯은 이제 "탐색기"용이다 —
        # searxng 로 두면 사양→모델 탐색(설계 2번)이 켜진다. 가격은 여전히 전용 파서가 매긴다.
        "searchProvider": os.getenv("SEARCH_PROVIDER") or "searxng",
        "searchUrl": os.getenv("SEARCH_URL") or os.getenv("STUDYWEB_URL") or "http://localhost:8888",
        "searchKey": os.getenv("STUDYWEB_API_KEY") or "",
        "searchPlatforms": os.getenv("SEARCH_PLATFORMS") or DEFAULT_PLATFORMS,
        "pricePrompt": os.getenv("PRICE_PROMPT") or "",
        "priceSources": os.getenv("PRICE_SOURCES") or DEFAULT_PRICE_SOURCES,
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
        "priceSources": cfg.get("priceSources") or "",
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

# 백엔드가 기대하는 g2b.ai.timeout-ms 기본값(초). 아래 데드라인의 상한이다.
BACKEND_TIMEOUT_SECONDS = 120.0

# LLM 호출 하나의 데드라인. `CLAUDE.md §2` 규칙 7 이 요구하는 순서는
#
#     AI 자체 데드라인  <  g2b.ai.timeout-ms(120초)  <  백엔드 리스(300초)
#
# 이 순서가 뒤집히면 백엔드는 120초에 포기하는데 우리는 계속 생성하고 있고, 리스가 만료된
# 뒤 다른 워커가 같은 작업을 다시 집는다 — 같은 공고를 두 번 추론해 LLM 비용이 두 배로 난다.
# 한때 이 값이 300초로 박혀 있어 정확히 그 상태였다.
#
# 양쪽을 함께 늘릴 때는 백엔드의 AI_TIMEOUT_MS 와 ANALYSIS_LEASE_MS 도 같이 올려야 한다.
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS") or 100)


def llm_headers() -> dict:
    """외부 OpenAI 호환 API 용 인증 헤더. 로컬 LM Studio 는 키가 없다."""
    key = get_ai_config().get("llmApiKey")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _parse_sources(raw: str) -> list[str]:
    return [s.strip().lower() for s in str(raw or "").split(",") if s.strip()]


def price_sources() -> list[str]:
    """멀티소스 가격 리졸버가 켤 소스 이름들 — 파일 > 환경변수 > 기본값(config 우선순위).

    price.resolve 가 이 목록으로 SOURCES 레지스트리를 거른다. 값이 비면 기본값으로 되돌린다.
    """
    return _parse_sources(get_ai_config().get("priceSources")) or _parse_sources(DEFAULT_PRICE_SOURCES)


#: 편의 상수 — 환경변수/기본값에서 파생. 파일 오버라이드까지 반영하려면 price_sources() 를 쓴다.
PRICE_SOURCES = _parse_sources(os.getenv("PRICE_SOURCES") or DEFAULT_PRICE_SOURCES)
