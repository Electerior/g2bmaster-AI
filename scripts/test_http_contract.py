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

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import config as app_config  # noqa: E402
from app.errors import AiFailure  # noqa: E402
from app.main import app  # noqa: E402
from app.price import resolve as resolve_price  # noqa: E402
from app.pricecommon import make_quote  # noqa: E402
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


# ── 요약 계열이 요약을 지어내지 않는가 ──────────────────────────────────────
# 과거 회귀: 두 핸들러가 payload 에 이름만 있으면 "…요약이 완료되었습니다" 라는 지어낸
# 문장을 aiFallback=false 로 200 에 실어 보냈다. AiClient 는 aiDisabled/aiFallback 만
# 걸러내므로 그것을 **성공으로 판정**하고, AnalysisJobRunner 가 analysis_history 에
# 적재한 뒤 작업을 완료 처리한다 — 재사용 키가 같으니 영원히 재분석되지 않는다.
#
# 위 NOT_PORTED_POSTS 루프가 501 을 이미 확인하므로 여기서는 '내용이 있는 본문'이
# 새어 나가지 않는지만 본다. 실제 분석을 이식할 때 이 블록을 200 검증으로 바꾼다.
for path in ("/api/item-summary", "/api/bid-summary"):
    body = client.post(path, json={"bidNtceNo": "20260101", "bidNtceNm": "서버 구매", "itemName": "GPU 서버"}).json()
    check(
        not body.get("summary"),
        f"{path} 는 분석을 이식하기 전까지 summary 를 지어내면 안 됩니다(캐시에 눌러앉습니다).",
    )
    check(
        "aiFallback" not in body and "aiDisabled" not in body,
        f"{path} 미구현 응답을 폴백처럼 위장하면 안 됩니다.",
    )
    check(bool(body.get("requestId")), f"{path} 는 requestId 가 있어야 로그 대조가 됩니다.")

# ── 가격 조회 형식 (네트워크 없이 되는 것만) ────────────────────────────────
# 실제 다나와 조회는 환경에 흔들리므로 여기서 하지 않는다(smoke_price.py 가 따로 한다).
# 형식 검증만 본다 — 이건 외부 호출 이전 단계라 결정적이다.
price_bad = client.post("/api/price/resolve", json={})
check(price_bad.status_code == 400, "itemName 없는 price/resolve 는 400 이어야 합니다.")
check(price_bad.json().get("code") == "BAD_REQUEST", "itemName 누락은 BAD_REQUEST 입니다.")

price_url_bad = client.post("/api/price/url", json={"url": "https://coupang.com/x?pcode=1"})
check(price_url_bad.status_code == 400, "다나와가 아닌 URL 은 400 이어야 합니다.")
check(
    price_url_bad.json().get("code") == "UNSUPPORTED_SOURCE",
    "화이트리스트 밖 도메인은 UNSUPPORTED_SOURCE 여야 합니다(재시도 금지).",
)
check(
    price_url_bad.json().get("retryable") is False,
    "UNSUPPORTED_SOURCE 는 4xx 영구 실패다 — retryable 이면 워커가 헛돈다.",
)

# ── 가격 집계 (오프라인, 인메모리 가짜 소스) ─────────────────────────────────
# 실제 소스는 환경에 흔들리므로 여기서 치지 않는다. resolve(sources=[...]) 에 가짜 소스를
# 주입해 봉투 키와 세 갈래(모두 빈 / 부분 실패 / 모두 실패)를 결정적으로 확인한다.
async def _agg_empty(item_name, deadline_ms=None):
    return []


async def _agg_boom(item_name, deadline_ms=None):
    raise AiFailure("PRICE_SOURCE_BROKEN", detail="offline")


async def _agg_one(item_name, deadline_ms=None):
    return [make_quote(source="danawa", sourceId="1",
                       url="https://prod.danawa.com/info/?pcode=1", name="RTX 5090", priceKrw=1000)]


agg_empty = asyncio.run(resolve_price({"itemName": "x"}, sources=[("danawa", _agg_empty), ("enuri", _agg_empty)]))
check(
    set(agg_empty) == {"quotes", "searchInfo", "degraded", "degradedReasons"},
    "가격 응답 봉투 키는 정확히 {quotes, searchInfo, degraded, degradedReasons} 여야 합니다.",
)
check(agg_empty["searchInfo"]["status"] == "not-found", "모든 소스가 빈 검색이면 not-found 여야 합니다.")
check(agg_empty["degraded"] is False, "실패가 없으면 degraded 는 False 여야 합니다.")
check(agg_empty["degradedReasons"] == [], "실패가 없으면 degradedReasons 는 비어야 합니다.")

agg_partial = asyncio.run(resolve_price({"itemName": "x"}, sources=[("danawa", _agg_one), ("enuri", _agg_boom)]))
check(agg_partial["searchInfo"]["status"] == "found", "한 소스라도 결과가 있으면 found 여야 합니다.")
check(agg_partial["degraded"] is True, "한 소스가 실패하고 다른 소스가 성공하면 degraded True 여야 합니다.")
check(
    len(agg_partial["degradedReasons"]) == 1 and agg_partial["degradedReasons"][0].get("source") == "enuri",
    "실패한 소스가 degradedReasons 에 실려야 합니다.",
)
check(
    agg_partial["degradedReasons"][0].get("code") == "PRICE_SOURCE_BROKEN",
    "degradedReasons 에 실패 코드가 있어야 합니다.",
)

agg_all_failed = False
try:
    asyncio.run(resolve_price({"itemName": "x"}, sources=[("danawa", _agg_boom), ("enuri", _agg_boom)]))
except AiFailure as agg_error:
    agg_all_failed = agg_error.code == "PRICE_SOURCE_BROKEN"
check(agg_all_failed, "0건 + 모든 소스 실패는 PRICE_SOURCE_BROKEN 을 올려야 합니다(200 으로 포장 금지).")

agg_no_source = asyncio.run(resolve_price({"itemName": "x"}, sources=[]))
check(agg_no_source["searchInfo"]["status"] == "no-source", "켜진 소스가 없으면 no-source 여야 합니다(에러 아님).")

# requestId 우선순위: 본문 > state > 헤더
check(
    client.post("/api/bid-summary", json={"requestId": "rid-body"}, headers={"X-Request-Id": "rid-header"}).json()["requestId"]
    == "rid-body",
    "payload.requestId 가 X-Request-Id 헤더보다 우선해야 합니다.",
)


# ── 실패 본문의 모양 ─────────────────────────────────────────────────────────
# 예전에는 갈래마다 본문이 달라(501·503·404·401·422 가 전부 다른 모양) 백엔드가 하나의
# 파서로 읽을 수 없었다. 네 필드가 모든 갈래에서 나오는지 본다 — docs/failure-modes.md.
def check_failure_shape(response, expected_code: str, where: str) -> None:
    payload = response.json()
    check(
        payload.get("code") == expected_code,
        f"{where}: code 는 {expected_code} 여야 합니다(받은 값 {payload.get('code')!r}).",
    )
    check(bool(payload.get("error")), f"{where}: 사용자에게 보일 한국어 문구가 있어야 합니다.")
    check(isinstance(payload.get("retryable"), bool), f"{where}: retryable 이 있어야 재시도 여부를 판단합니다.")
    check(bool(payload.get("requestId")), f"{where}: requestId 가 없으면 두 저장소의 로그를 대조할 수 없습니다.")
    check("detail" not in payload, f"{where}: 내부 진단(detail)은 본문에 나가면 안 됩니다.")


check_failure_shape(client.post("/api/legal/review-clauses", json={}), "NOT_PORTED", "501 미구현")
check(
    client.post("/api/legal/review-clauses", json={}).json()["retryable"] is False,
    "미구현은 다시 불러도 결과가 같습니다 — retryable 이 참이면 워커의 재시도 예산을 태웁니다.",
)

# 우리가 소유하지 않는 경로. 예전엔 {error, path} 라 code 가 없었다.
check_failure_shape(client.get("/api/does-not-exist"), "BAD_REQUEST", "404 미등록 경로")

# 본문 검증 실패. 예전엔 FastAPI 기본 422 {detail:[...]} 라 code·error 둘 다 없었다.
bad_body = client.post("/api/bid-summary", content=b"[]", headers={"Content-Type": "application/json"})
check(bad_body.status_code == 400, f"본문이 계약과 다르면 400 이어야 합니다(받은 값 {bad_body.status_code}).")
check_failure_shape(bad_body, "BAD_REQUEST", "본문 검증 실패")

# requestId 는 백엔드가 준 값을 그대로 되돌려 준다. 새로 만들면 로그를 이을 수 없다.
check(
    client.post("/api/bid-summary", json={}, headers={"X-Request-Id": "rid-header"}).json()["requestId"] == "rid-header",
    "X-Request-Id 헤더를 그대로 되돌려 줘야 합니다.",
)
check(
    client.post("/api/bid-summary", json={"requestId": "rid-body"}).json()["requestId"] == "rid-body",
    "본문의 requestId 가 헤더보다 우선해야 합니다(백엔드가 payload 에 싣는 경로).",
)

# 프롬프트 버전 맵 — 단일 문자열은 업무구분이 갈리는 순간 깨진다.
version_map = version.json().get("versions")
check(isinstance(version_map, dict), "versions 맵이 있어야 합니다(엔드포인트 × 문서종류로 갈립니다).")
check(
    isinstance(version_map, dict) and version_map.get("item-summary") == ITEM_SUMMARY_PROMPT_VERSION,
    "versions 의 item-summary 는 기존 promptVersion 과 같아야 합니다(캐시 호환).",
)

# ── 데드라인 순서 ────────────────────────────────────────────────────────────
# CLAUDE.md §2 규칙 7: AI 자체 데드라인 < g2b.ai.timeout-ms < 백엔드 리스.
# 한때 LLM 호출이 300초로 박혀 있어 백엔드(120초)가 먼저 포기했고, 리스가 만료되면 다른
# 워커가 같은 공고를 다시 추론했다 — LLM 비용이 두 배로 나는 상태였다. 값을 늘릴 때는
# 백엔드의 AI_TIMEOUT_MS·ANALYSIS_LEASE_MS 도 함께 올려야 한다.
check(
    app_config.LLM_TIMEOUT_SECONDS < app_config.BACKEND_TIMEOUT_SECONDS,
    "LLM 데드라인({}초)이 백엔드 타임아웃({}초) 이상입니다 — 같은 작업을 두 번 추론하게 됩니다.".format(
        app_config.LLM_TIMEOUT_SECONDS, app_config.BACKEND_TIMEOUT_SECONDS
    ),
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
