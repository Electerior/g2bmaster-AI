"""실패 분류의 단일 출처.

규칙(`CLAUDE.md §2-1`): AI→백엔드 구간에서 실패는 정직한 4xx/5xx 로 내린다.
HTTP 200 폴백은 백엔드 컨트롤러가 `AiUnavailableException` 을 잡아 만드는 것이다.

**백엔드는 status 가 아니라 `code` 와 `retryable` 로 판단해야 한다.**
status 는 프록시·로드밸런서·모니터링이 읽는 값이고 분류의 진실은 본문에 있다.

> 2026-08-06 확인 — 현재 `AiClient` 는 `catch (RestClientException e)` 로 4xx·5xx 를
> 뭉뚱그려 잡고 `e.getMessage()` 만 남긴다. 즉 **이 본문을 아직 읽지 않는다.**
> 그래도 모양을 통일하는 이유는 둘이다: 우리 쪽 실패 경로가 다섯 갈래로 갈리는 것을 막고,
> 백엔드가 읽기 시작할 때 계약이 이미 서 있게 하려는 것이다(`decisions.md F-4`).

전체 표와 각 분류를 언제 쓰는지는 `docs/failure-modes.md`. 값이 어긋나면
`scripts/test_http_contract.py` 가 먼저 깨진다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Request


@dataclass(frozen=True)
class FailureSpec:
    status: int
    retryable: bool
    #: 사용자에게 그대로 렌더링되는 한국어(`aiError`). 코드에서 조립하지 않는다(`CLAUDE.md §2-9`).
    message: str


FAILURES: dict[str, FailureSpec] = {
    "BAD_REQUEST": FailureSpec(400, False, "요청 형식이 올바르지 않습니다."),
    "INPUT_TOO_LARGE": FailureSpec(400, False, "문서 분량이 너무 많아 분석할 수 없습니다."),
    "TAG_MISSING": FailureSpec(400, False, "문서 태그가 확인되지 않아 서약서 검토를 시작할 수 없습니다."),
    "UNAUTHORIZED": FailureSpec(401, False, "AI 서비스 호출 권한이 없습니다."),
    # 아직 이식하지 않은 표면. 501 을 유지하는 근거는 decisions.md F-4 —
    # AiClient 가 status 로 분기하지 않으므로 501 도 AiUnavailableException 경로를 탄다.
    # retryable=false 인 이유: 지금 다시 불러도 결과가 같다. 워커가 재시도 예산을 태우면
    # 큐의 다른 작업이 굶는다. status 와 retryable 이 갈리는 것은 의도된 것이다.
    "NOT_PORTED": FailureSpec(501, False, "AI 분석 기능이 아직 준비되지 않았습니다."),
    # 임베딩만 못 하는 상태. 나머지 표면은 정상이므로 서비스 전체를 실패로 보면 안 된다.
    "EMBEDDING_UNAVAILABLE": FailureSpec(503, False, "임베딩 기능을 사용할 수 없습니다."),
    "LLM_UNAVAILABLE": FailureSpec(503, True, "AI 분석 서버에 연결할 수 없어 분석을 완료하지 못했습니다."),
    "LLM_TIMEOUT": FailureSpec(504, True, "AI 분석이 제한 시간 내에 끝나지 않았습니다."),
    "LLM_MALFORMED": FailureSpec(502, True, "AI 응답을 해석하지 못했습니다."),
    "INTERNAL": FailureSpec(500, True, "분석 처리 중 오류가 발생했습니다."),
}


class AiFailure(Exception):
    """분류된 실패. `detail` 은 로그 전용이고 응답 본문에 넣지 않는다.

    본문에 넣으면 파일 경로·엔드포인트 주소가 그대로 사용자 화면에 나간다.
    `extra` 는 그 분류에만 있는 부가 정보(`reason`·`blockedBy` 등)다.
    """

    def __init__(
        self,
        code: str,
        detail: str = "",
        retry_after_ms: int | None = None,
        **extra: object,
    ) -> None:
        spec = FAILURES.get(code) or FAILURES["INTERNAL"]
        super().__init__(spec.message)
        self.code = code if code in FAILURES else "INTERNAL"
        self.detail = detail
        self.retry_after_ms = retry_after_ms
        self.extra = {key: value for key, value in extra.items() if value not in (None, "")}

    @property
    def spec(self) -> FailureSpec:
        return FAILURES[self.code]


def resolve_request_id(request: Request, payload: object = None) -> str:
    """백엔드가 준 요청 ID 를 최우선으로 쓴다.

    이 값은 두 저장소의 로그를 잇는 유일한 실이다. 여기서 새로 만들어 버리면
    백엔드 로그의 작업 하나와 우리 로그의 요청 하나를 이어 볼 방법이 사라진다.

    본문 > 이미 정해진 값 > 헤더 > 새로 발급 순서. 본문을 먼저 보는 이유는 백엔드가
    payload 에 실어 보내는 경우가 있어서다 — 미들웨어는 본문을 읽을 수 없으므로 라우트가 넘겨준다.

    `state` 가 헤더보다 앞서야 한다. 라우트가 본문에서 건진 값을 state 에 넣어 두는데,
    헤더를 먼저 보면 응답을 조립하는 쪽(payload 를 못 받는다)이 그 값을 도로 헤더로 덮는다.
    """
    if isinstance(payload, dict):
        from_body = payload.get("requestId")
        if isinstance(from_body, str) and from_body:
            return from_body

    return (
        getattr(request.state, "request_id", "")
        or request.headers.get("x-request-id")
        or str(uuid.uuid4())
    )


def to_body(failure: AiFailure, request_id: str) -> dict:
    """응답 본문. 다섯 갈래로 갈리던 실패 모양을 여기 하나로 모은다."""
    # extra 를 먼저 깔고 핵심 네 필드로 덮는다. 순서가 반대면 `reason` 옆에 실수로 낀
    # `retryable` 하나가 분류를 조용히 뒤집어 백엔드가 영구 실패를 무한 재시도한다.
    body: dict = {
        **failure.extra,
        "code": failure.code,
        "error": failure.spec.message,
        "retryable": failure.spec.retryable,
        "requestId": request_id,
    }
    # retryAfterMs 는 있을 때만 싣는다. 없는데 0 을 실으면 "즉시 재시도" 로 읽힌다.
    if failure.retry_after_ms is not None:
        body["retryAfterMs"] = failure.retry_after_ms
    return body


def demo() -> None:
    """self-check — 표의 불변식만 건다. `python app/errors.py`."""

    class _FakeRequest:
        def __init__(self) -> None:
            self.headers: dict = {}
            self.state = type("S", (), {})()

    request = _FakeRequest()

    for code, spec in FAILURES.items():
        assert 400 <= spec.status < 600, f"{code}: status 가 실패 범위를 벗어났다"
        assert spec.message, f"{code}: 한국어 문구가 비어 있다"
        # 4xx 는 영구 실패다. 재시도 가능한 4xx 를 만들면
        # 백엔드가 절대 성공할 수 없는 요청을 예산이 다할 때까지 반복한다.
        if spec.status < 500:
            assert not spec.retryable, f"{code}: 4xx 인데 retryable 이다"

    body = to_body(AiFailure("NOT_PORTED", "detail", reason="아직", blockedBy=""), "rid-1")
    assert body["code"] == "NOT_PORTED" and body["retryable"] is False
    assert body["requestId"] == "rid-1" and body["reason"] == "아직"
    assert "blockedBy" not in body, "빈 값은 싣지 않는다"
    assert "detail" not in body, "내부 진단은 본문에 나가면 안 된다"
    assert "retryAfterMs" not in body, "없으면 아예 싣지 않는다"

    assert AiFailure("존재하지않는코드").code == "INTERNAL", "모르는 코드는 INTERNAL 로 떨어진다"

    assert resolve_request_id(request, {"requestId": "from-body"}) == "from-body"
    request.headers = {"x-request-id": "from-header"}
    assert resolve_request_id(request, {}) == "from-header"

    print("app/errors.py: OK")


if __name__ == "__main__":
    demo()
