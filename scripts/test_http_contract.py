#!/usr/bin/env python3
"""HTTP 계약 테스트.

백엔드 `integration/ai/AiClient` 가 부르는 11개 경로가 **전부 존재하고**, 아직 이식하지 않은
경로는 200 이 아니라 501 `NOT_PORTED` 로 응답하는지 확인한다.

이 구분이 중요한 이유: `AiClient.itemSummary` 는 `aiFallback` 응답을 성공으로 치지 않는다.
미구현을 200 으로 위장하면 폴백 결과가 `analysis_history` 에 눌러앉아 영원히 재분석되지
않는다(ai-boundary.md §6.3).
"""

from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.pop("AI_SERVICE_SECRET", None)
os.environ.pop("INTERNAL_SECRET", None)
# 개발 PC 에 LM Studio 가 떠 있으면 결과가 달라진다. 계약 검증은 환경에 흔들리면 안 되므로
# 확실히 닫힌 주소로 고정한다(라이브 확인은 scripts/smoke_llm.py 가 따로 한다).
os.environ["LMS_BASE"] = "http://127.0.0.1:9"
os.environ.pop("LLM_WORKERS", None)
os.environ["ATTACHMENT_CACHE_DIR"] = os.path.join(str(ROOT), ".pytest_cache", "contract")

from fastapi.testclient import TestClient  # noqa: E402

from app import config as app_config  # noqa: E402
from app.main import app  # noqa: E402
from app.prompts import ITEM_SUMMARY_PROMPT_VERSION  # noqa: E402

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


client = TestClient(app)

# ── 상태 ─────────────────────────────────────────────────────────────────────
for path in ("/health", "/healthz"):
    response = client.get(path)
    check(response.status_code == 200 and response.json()["ok"] is True, f"{path} 는 200 ok 여야 합니다.")

# ── 구현된 경로 ──────────────────────────────────────────────────────────────
version = client.get("/api/ai/prompt-version")
check(version.status_code == 200, "/api/ai/prompt-version 는 200 이어야 합니다.")
check(
    version.json()["promptVersion"] == ITEM_SUMMARY_PROMPT_VERSION,
    "프롬프트 버전은 원본 값과 같아야 합니다(다르면 기존 분석 캐시가 전부 무효화됩니다).",
)
check(
    version.json()["promptVersion"] == "item-summary-2026-08-04-v4",
    "프롬프트 버전이 원본 lib/analysis-history.js 의 값과 달라졌습니다 — 기존 캐시가 고아가 됩니다.",
)

# LM Studio 가 없는 환경이라 워커는 전부 비정상이지만, 응답 모양은 지켜져야 한다.
capacity = client.get("/api/ai/capacity")
check(capacity.status_code == 200, "/api/ai/capacity 는 LLM 이 없어도 200 이어야 합니다.")
body = capacity.json()
check(isinstance(body.get("capacity"), int), "capacity 는 정수여야 합니다(ETA 계산에 쓰입니다).")
check(body["capacity"] == 0, "도달 불가 워커의 용량은 0 이어야 합니다 — ETA 가 부풀면 안 됩니다.")
check(isinstance(body.get("workers"), list), "워커 상태 목록이 있어야 합니다.")

models = client.get("/api/llm/models")
check(models.status_code == 200, "/api/llm/models 는 LLM 이 없어도 200 이어야 합니다.")
check(models.json()["reachable"] is False, "LLM 서버가 없으면 reachable=false 여야 합니다.")
check(isinstance(models.json().get("models"), list), "모델 목록은 배열이어야 합니다.")

empty_embed = client.post("/api/embed", json={"texts": []})
check(empty_embed.status_code == 200, "빈 임베딩 요청은 200 이어야 합니다.")
check(empty_embed.json()["vectors"] == [], "빈 입력에는 빈 벡터를 돌려줘야 합니다.")

embed = client.post("/api/embed", json={"texts": ["서버 구매"]})
check(
    embed.status_code in (200, 503),
    "임베딩은 성공(200)하거나 ML 스택 부재를 503 으로 알려야 합니다.",
)
if embed.status_code == 503:
    check(embed.json()["code"] == "EMBEDDING_UNAVAILABLE", "임베딩 불가 사유 코드가 있어야 합니다.")

# ── 아직 이식하지 않은 경로 ──────────────────────────────────────────────────
NOT_PORTED_POSTS = [
    "/api/item-summary",
    "/api/bid-summary",
    "/api/legal/review-clauses",
    "/api/legal/outreach-draft",
    "/api/pledge/revision-workflow",
    "/api/price/resolve",
    "/api/price/url",
]
for path in NOT_PORTED_POSTS:
    response = client.post(path, json={})
    check(response.status_code == 501, f"{path} 는 미구현이므로 501 이어야 합니다.")
    payload = response.json()
    check(payload.get("code") == "NOT_PORTED", f"{path} 는 NOT_PORTED 코드를 달아야 합니다.")
    check(bool(payload.get("reason")), f"{path} 는 이식되지 않은 이유를 밝혀야 합니다.")
    check(
        "aiFallback" not in payload and "aiDisabled" not in payload,
        f"{path} 미구현 응답을 폴백처럼 위장하면 안 됩니다(캐시에 눌러앉습니다).",
    )

# 계약의 11개 경로가 하나도 빠지지 않았는지 확인한다.
CONTRACT = {
    ("POST", "/api/item-summary"), ("POST", "/api/bid-summary"),
    ("POST", "/api/legal/review-clauses"), ("POST", "/api/legal/outreach-draft"),
    ("POST", "/api/pledge/revision-workflow"), ("POST", "/api/price/resolve"),
    ("POST", "/api/price/url"), ("POST", "/api/embed"),
    ("GET", "/api/ai/prompt-version"), ("GET", "/api/ai/capacity"), ("GET", "/api/llm/models"),
}
registered = {
    (method, route.path)
    for route in app.routes
    for method in getattr(route, "methods", set())
}
missing = CONTRACT - registered
check(not missing, f"계약에 있는 경로가 등록되지 않았습니다: {sorted(missing)}")

# ── 호출자 인증 ──────────────────────────────────────────────────────────────
app_config.SERVICE_SECRET = "s3cr3t"
import app.main as main_module  # noqa: E402

main_module.SERVICE_SECRET = "s3cr3t"
check(client.get("/api/ai/prompt-version").status_code == 401, "시크릿 설정 시 헤더 없는 요청은 401 이어야 합니다.")
check(
    client.get("/api/ai/prompt-version", headers={"X-Internal-Secret": "s3cr3t"}).status_code == 200,
    "원본과 같은 X-Internal-Secret 헤더를 받아야 합니다.",
)
check(
    client.get("/api/ai/prompt-version", headers={"Authorization": "Bearer s3cr3t"}).status_code == 200,
    "원본 analysis-executor 가 쓰던 Bearer 토큰도 받아야 합니다.",
)
check(client.get("/health").status_code == 200, "/health 는 시크릿 없이도 열려 있어야 합니다.")
main_module.SERVICE_SECRET = ""
app_config.SERVICE_SECRET = ""

if failures:
    for failure in failures:
        print(f"- FAIL {failure}", file=sys.stderr)
    print(f"test_http_contract: {len(failures)}건 실패", file=sys.stderr)
    sys.exit(1)

print("test_http_contract: OK")
