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
    "llmMaxTokens", "llmContextWindow",
]
KEY_FIELDS = {"llmApiKey"}



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
    return out


def get_ai_config() -> dict:
    return resolve_config(read_file(), env_defaults())




def mask_config(cfg: dict) -> dict:
    """브라우저 노출용 — 키는 마스킹하고 설정 여부만 알린다."""
    def mask(value):
        text = str(value or "")
        return ("••••" + text[-4:]) if text else ""

    return {
        "llmBase": cfg.get("llmBase"),
        "llmModel": cfg.get("llmModel"),
        "llmContextWindow": cfg.get("llmContextWindow"),
        "llmApiKey": mask(cfg.get("llmApiKey")),
        "llmApiKeySet": bool(cfg.get("llmApiKey")),
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


