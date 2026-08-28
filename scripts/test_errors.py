#!/usr/bin/env python3
"""실패 계약 단위 테스트 — `app/errors.py` 와 `app/main.py` 의 예외 핸들러.

`test_http_contract.py` 는 **11개 표면이 있는지**를 보고, 이 파일은 **실패 한 건의 모양**을 본다.

여기서 막는 것 네 가지:

1. `docs/failure-modes.md §2` 표와 코드가 갈리는 것 — 표를 파싱해 `FAILURES` 와 대조한다.
2. 4xx 가 재시도 대상이 되는 것 — 절대 성공할 수 없는 요청을 예산이 다할 때까지 반복한다.
3. `requestId` 가 중간에 새로 발급되는 것 — 두 저장소의 로그를 잇는 유일한 실이다.
4. 내부 진단(`detail`)이 본문으로 새는 것 — 파일 경로·엔드포인트 주소가 사용자 화면에 나간다.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# test_http_contract.py 와 같은 이유로 환경을 고정한다 — 개발 PC 에 LM Studio 가 떠 있어도
# 결과가 흔들리면 안 된다.
os.environ.pop("AI_SERVICE_SECRET", None)
os.environ.pop("INTERNAL_SECRET", None)
os.environ["LMS_BASE"] = "http://127.0.0.1:9"
# LLM_WORKERS 는 pop 하면 안 된다 — app/config.py 의 load_dotenv() 가 그 직후 .env 에서
# 되살려 놓는다(dotenv 는 "이미 있는" 키만 덮지 않는다). 실제로 이 구멍 때문에 계약
# 테스트가 개발 PC 의 LM Studio 에 붙어 요약을 진짜로 생성한 적이 있다. 닫힌 주소로 덮는다.
os.environ["LLM_WORKERS"] = "http://127.0.0.1:9@1"
os.environ["ATTACHMENT_CACHE_DIR"] = os.path.join(str(ROOT), ".pytest_cache", "errors")

from fastapi.testclient import TestClient  # noqa: E402

from app import errors  # noqa: E402
from app.errors import FAILURES, AiFailure, resolve_request_id, to_body  # noqa: E402
from app.main import app  # noqa: E402
from app.prompts import NOTICE_SUMMARY_PROMPT_VERSION, PROMPT_VERSIONS  # noqa: E402

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


class FakeRequest:
    """`resolve_request_id` 가 만지는 두 가지(headers·state)만 흉내 낸다."""

    def __init__(self, headers: dict | None = None, request_id: str | None = None) -> None:
        self.headers = headers or {}
        self.state = types.SimpleNamespace()
        if request_id is not None:
            self.state.request_id = request_id


# ── 1. 모듈 self-check ───────────────────────────────────────────────────────
# `python app/errors.py` 로도 돌지만 make check 에서 빠지면 아무도 안 돌린다.
errors.demo()


# ── 2. 문서 ↔ 코드 결속 ──────────────────────────────────────────────────────
# docs/failure-modes.md 는 "값이 어긋나면 코드가 이긴다" 고 적어 두었다. 그 말이 참이려면
# 어긋났을 때 뭔가 깨져야 한다. 표를 파싱해 대조하는 것이 그 장치다.
doc = (ROOT / "docs" / "failure-modes.md").read_text(encoding="utf-8")
section = doc.split("## 2. 분류표")[-1].split("\n## ")[0]

documented: dict[str, tuple[int, bool]] = {}
for line in section.splitlines():
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) < 3 or not cells[1].isdigit():
        continue
    code = re.fullmatch(r"`(\w+)`", cells[0])
    if code:
        documented[code.group(1)] = (int(cells[1]), cells[2] == "✓")

check(bool(documented), "docs/failure-modes.md §2 분류표를 한 줄도 읽지 못했습니다 — 표 모양이 바뀌었습니다.")
check(
    set(documented) == set(FAILURES),
    "문서와 코드의 분류 집합이 다릅니다 — "
    f"문서에만 {sorted(set(documented) - set(FAILURES))}, 코드에만 {sorted(set(FAILURES) - set(documented))}",
)
for code, (status, retryable) in documented.items():
    spec = FAILURES.get(code)
    if spec is None:
        continue
    check(spec.status == status, f"{code}: 문서는 {status}, 코드는 {spec.status} 입니다.")
    check(spec.retryable == retryable, f"{code}: 문서의 retryable 은 {retryable}, 코드는 {spec.retryable} 입니다.")


# ── 3. AiFailure ─────────────────────────────────────────────────────────────
unknown = AiFailure("이런코드는없다")
check(unknown.code == "INTERNAL", "모르는 코드는 INTERNAL 로 떨어져야 합니다 — KeyError 로 죽으면 안 됩니다.")
check(unknown.spec is FAILURES["INTERNAL"], "떨어진 분류의 spec 도 INTERNAL 이어야 합니다.")

# extra 는 그 분류에만 있는 부가 정보다. 빈 값을 실으면 백엔드가 "값이 있는데 비었다" 와
# "아예 없다" 를 구분하지 못한다.
sparse = to_body(AiFailure("NOT_PORTED", reason="아직", blockedBy="", note=None, count=0), "rid")
check(sparse.get("reason") == "아직", "채워진 extra 는 실어야 합니다.")
check("blockedBy" not in sparse and "note" not in sparse, "빈 문자열·None extra 는 싣지 않습니다.")
check(sparse.get("count") == 0, "0 은 빈 값이 아닙니다 — 실려야 합니다.")

# extra 가 핵심 필드를 덮으면 백엔드의 분류가 조용히 뒤집힌다.
# `code`·`detail` 은 생성자 인자라 Python 이 TypeError 로 먼저 막아 준다. 나머지 셋은 안 막힌다.
hijacked = to_body(AiFailure("NOT_PORTED", retryable=True, error="딴소리", requestId="가짜"), "rid-real")
check(hijacked["code"] == "NOT_PORTED", "extra 가 code 를 덮으면 안 됩니다.")
check(hijacked["retryable"] is False, "extra 가 retryable 을 덮으면 영구 실패가 무한 재시도됩니다.")
check(hijacked["error"] == FAILURES["NOT_PORTED"].message, "extra 가 화면 문구를 덮으면 안 됩니다.")
check(hijacked["requestId"] == "rid-real", "extra 가 requestId 를 덮으면 로그를 이을 수 없습니다.")

check("retryAfterMs" not in to_body(AiFailure("LLM_TIMEOUT"), "rid"), "없으면 아예 싣지 않습니다.")
check(
    to_body(AiFailure("LLM_UNAVAILABLE", retry_after_ms=0), "rid").get("retryAfterMs") == 0,
    "명시한 0 은 '즉시 재시도' 라는 뜻이므로 실어야 합니다 — 없는 것과 다릅니다.",
)
check(
    "detail" not in to_body(AiFailure("INTERNAL", detail="C:/secret/key.json"), "rid"),
    "내부 진단은 본문에 나가면 안 됩니다(경로가 사용자 화면에 찍힙니다).",
)

for code, spec in FAILURES.items():
    check(str(AiFailure(code)) == spec.message, f"{code}: 예외 메시지는 분류표의 한국어 문구여야 합니다.")


# ── 4. requestId 우선순위 ────────────────────────────────────────────────────
both = FakeRequest(headers={"x-request-id": "from-header"}, request_id="from-state")
check(resolve_request_id(both, {"requestId": "from-body"}) == "from-body", "본문이 가장 우선입니다.")
check(
    resolve_request_id(both) == "from-state",
    "라우트가 state 에 넣어 둔 값이 헤더보다 우선해야 합니다 — "
    "응답을 조립하는 쪽은 payload 를 받지 못하므로 여기서 뒤집히면 본문 우선이 무효가 됩니다.",
)
check(resolve_request_id(FakeRequest(headers={"x-request-id": "h"})) == "h", "state 가 없으면 헤더를 씁니다.")
check(resolve_request_id(both, {"requestId": 12345}) == "from-state", "문자열이 아닌 본문 값은 무시합니다.")
check(resolve_request_id(both, {"requestId": ""}) == "from-state", "빈 본문 값은 무시합니다.")
check(resolve_request_id(both, "본문이 dict 가 아님") == "from-state", "dict 가 아닌 payload 에 죽으면 안 됩니다.")

fresh = [resolve_request_id(FakeRequest()) for _ in range(2)]
check(all(fresh) and fresh[0] != fresh[1], "아무 단서가 없으면 매번 새로 발급해야 합니다.")


# ── 5. 예외 핸들러가 실제로 물려 있는가 ──────────────────────────────────────
# 이 라우트는 이 테스트 프로세스에만 존재한다. 커밋된 app 에는 없다.
@app.post("/__test_raises__")
async def _raises(payload: dict):
    raise ValueError("C:/secret/key.json 이 새면 안 된다")


client = TestClient(app, raise_server_exceptions=False)

# 로그를 가로챈다. 목적이 둘이다 — 일부러 터뜨린 예외의 스택이 make check 출력을 덮지 않게 하고,
# `INTERNAL` 이 실제로 스택을 남기는지(분류표의 "로그로 알릴 것") 확인한다.
logged: list[logging.LogRecord] = []


class _Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        logged.append(record)


app_logger = logging.getLogger("g2bmaster-ai")
app_logger.handlers = [_Capture()]
app_logger.propagate = False


def shape(response, expected_code: str, expected_status: int, where: str) -> dict:
    payload = response.json()
    check(
        response.status_code == expected_status,
        f"{where}: status 는 {expected_status} 여야 합니다(받은 값 {response.status_code}).",
    )
    check(payload.get("code") == expected_code, f"{where}: code 는 {expected_code} 여야 합니다(받은 값 {payload.get('code')!r}).")
    check(bool(payload.get("error")), f"{where}: 사용자에게 보일 한국어 문구가 있어야 합니다.")
    check(isinstance(payload.get("retryable"), bool), f"{where}: retryable 이 있어야 재시도 여부를 판단합니다.")
    check(bool(payload.get("requestId")), f"{where}: requestId 가 없으면 두 저장소의 로그를 대조할 수 없습니다.")
    check("detail" not in payload, f"{where}: 내부 진단은 본문에 나가면 안 됩니다.")
    return payload


# 분류되지 않은 예외 = 우리 버그. 500 이되 예외 메시지는 새지 않아야 한다.
boom = client.post("/__test_raises__", json={}, headers={"X-Request-Id": "rid-boom"})
body = shape(boom, "INTERNAL", 500, "핸들러 없는 예외")
check(body.get("retryable") is True, "우리 버그는 재시도 대상입니다(다음 배포에서 고쳐질 수 있습니다).")
check(body.get("requestId") == "rid-boom", "예외가 나도 헤더의 requestId 는 살아남아야 합니다.")
check("secret" not in boom.text, "예외 메시지에 든 내부 경로가 본문으로 새면 안 됩니다.")
check(
    any(record.exc_info for record in logged),
    "INTERNAL 은 스택을 로그에 남겨야 합니다 — 본문에서 감춘 원인을 볼 곳이 로그밖에 없습니다.",
)
check(bool(client.post("/__test_raises__", json={}).json().get("requestId")), "헤더가 없어도 requestId 는 발급돼야 합니다.")

# 헤더와 본문에 서로 다른 요청 ID 가 동시에 온 경우. test_http_contract.py 는 둘을 따로만 보내서
# 이 갈림을 못 본다 — 라우트는 본문 값을 골랐는데 응답 조립이 헤더 값으로 되돌리는 사고가 여기서 난다.
collide = client.post("/api/notice-summary", json={"requestId": "rid-body"}, headers={"X-Request-Id": "rid-header"})
check(collide.json().get("requestId") == "rid-body", "헤더와 본문이 다르면 본문이 이겨야 합니다.")

# 본문 자체가 없는 경우. FastAPI 기본 422 `{detail:[...]}` 를 덮지 못하면 code 도 error 도 없다.
shape(client.post("/api/notice-summary"), "BAD_REQUEST", 400, "본문 없음")

# 메서드 불일치(405). status 는 400 으로 뭉개지지만 백엔드는 code 로 판단한다
# — `docs/failure-modes.md §1`. 분류가 사라지지 않는 것이 요점이다.
shape(client.get("/api/bid-summary"), "BAD_REQUEST", 400, "메서드 불일치")

# 호출자 인증 실패. 예전엔 `{error}` 한 줄이라 code 도 requestId 도 없었다.
import app.main as main_module  # noqa: E402

main_module.SERVICE_SECRET = "s3cr3t"
denied = shape(client.get("/api/ai/prompt-version"), "UNAUTHORIZED", 401, "시크릿 불일치")
check(denied.get("retryable") is False, "설정 불일치는 다시 불러도 같습니다.")
check(
    shape(
        client.get("/api/ai/prompt-version", headers={"X-Request-Id": "rid-401"}),
        "UNAUTHORIZED",
        401,
        "시크릿 불일치(요청 ID 동반)",
    ).get("requestId")
    == "rid-401",
    "인증 실패도 백엔드가 준 requestId 를 되돌려 줘야 합니다.",
)
main_module.SERVICE_SECRET = ""


# ── 6. 프롬프트 버전 맵 ──────────────────────────────────────────────────────
check(PROMPT_VERSIONS.get("notice-summary") == NOTICE_SUMMARY_PROMPT_VERSION, "맵과 단일 값이 갈리면 캐시가 갈립니다.")
for key, value in PROMPT_VERSIONS.items():
    # 빈 문자열을 채우면 백엔드가 그것을 재사용 키에 넣고, 서로 다른 상태가 한 키로 묶인다.
    check(bool(key) and isinstance(value, str) and bool(value), f"프롬프트 버전 {key!r} 가 비어 있습니다.")


if failures:
    for failure in failures:
        print(f"- FAIL {failure}", file=sys.stderr)
    print(f"test_errors: {len(failures)}건 실패", file=sys.stderr)
    sys.exit(1)

print("test_errors: OK")
