#!/usr/bin/env python3
"""LLM 워커 풀 회귀 테스트 — 원본 `lib/llm-worker-pool.js` 의 동작을 잠근다.

여기서 지키는 것은 전부 "GPU 여러 대를 굴릴 때 조용히 처리량이 0 이 되는" 상황이다.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.llm.worker_pool import (  # noqa: E402
    LlmWorkerPool,
    is_retryable_llm_error,
    parse_worker_spec,
)

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


# ── 워커 설정 파싱 ───────────────────────────────────────────────────────────
workers = parse_worker_spec("http://a:1234@2, http://b:1234", "http://fallback:1234")
check(len(workers) == 2, "콤마로 나눈 워커를 모두 인식해야 합니다.")
check(workers[0]["capacity"] == 2, "@N 은 동시 처리 용량이어야 합니다.")
check(workers[1]["capacity"] == 1, "용량을 생략하면 1 이어야 합니다.")
check(workers[0]["id"] == "llm-1" and workers[1]["id"] == "llm-2", "워커 id 는 1부터 붙어야 합니다.")

check(
    parse_worker_spec("", "http://fallback:1234")[0]["base"] == "http://fallback:1234",
    "설정이 비면 기본 주소 한 대로 동작해야 합니다.",
)
check(
    parse_worker_spec("http://a:1234/@1", "x")[0]["base"] == "http://a:1234",
    "끝 슬래시는 제거돼야 합니다.",
)
check(parse_worker_spec("http://a@99", "x")[0]["capacity"] == 32, "용량 상한은 32 여야 합니다.")
check(parse_worker_spec("http://a@0", "x")[0]["capacity"] == 1, "용량 하한은 1 이어야 합니다.")

for bad, why in [("not-a-url@1", "형식이 잘못된 설정"), ("http://a,http://a", "중복 워커")]:
    try:
        parse_worker_spec(bad, "x")
        failures.append(f"{why}는 오류여야 합니다: {bad}")
    except ValueError:
        pass

# ── 재시도 판정 ──────────────────────────────────────────────────────────────
request = httpx.Request("POST", "http://a/v1/chat/completions")


def http_error(status: int) -> httpx.HTTPStatusError:
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


check(is_retryable_llm_error(http_error(500)), "5xx 는 다른 워커로 재시도해야 합니다.")
check(is_retryable_llm_error(http_error(429)), "429 는 재시도 대상입니다.")
check(is_retryable_llm_error(http_error(408)), "408 은 재시도 대상입니다.")
check(not is_retryable_llm_error(http_error(400)), "400 은 요청이 잘못된 것이라 재시도하면 안 됩니다.")
check(not is_retryable_llm_error(http_error(404)), "404 는 재시도 대상이 아닙니다.")
check(
    is_retryable_llm_error(httpx.ConnectError("refused", request=request)),
    "연결 거부는 워커가 죽은 것이므로 재시도해야 합니다.",
)


# ── 분배·페일오버 ────────────────────────────────────────────────────────────
async def main() -> None:
    # 용량이 큰 워커로 더 많이 간다(여유율 기준).
    pool = LlmWorkerPool(parse_worker_spec("http://a@2,http://b@1", "x"))
    used: list[str] = []

    async def slow(worker):
        used.append(worker["id"])
        await asyncio.sleep(0.02)
        return worker["id"]

    await asyncio.gather(*(pool.run(slow) for _ in range(6)))
    check(used.count("llm-1") > used.count("llm-2"), "용량이 큰 워커가 더 많은 작업을 받아야 합니다.")

    # 동시 실행이 용량을 넘지 않는다.
    pool = LlmWorkerPool(parse_worker_spec("http://a@2", "x"))
    peak = {"value": 0, "now": 0}

    async def track(worker):
        peak["now"] += 1
        peak["value"] = max(peak["value"], peak["now"])
        await asyncio.sleep(0.02)
        peak["now"] -= 1
        return None

    await asyncio.gather(*(pool.run(track) for _ in range(6)))
    check(peak["value"] <= 2, f"동시 실행이 용량(2)을 넘었습니다: {peak['value']}")

    # 죽은 워커는 쿨다운되고 살아 있는 워커로 넘어간다.
    pool = LlmWorkerPool(parse_worker_spec("http://dead@1,http://alive@1", "x"))
    attempts: list[str] = []

    async def fail_first(worker):
        attempts.append(worker["id"])
        if worker["id"] == "llm-1":
            raise httpx.ConnectError("refused", request=request)
        return "ok"

    result = await pool.run(fail_first)
    check(result == "ok", "재시도 가능한 실패는 다른 워커로 넘어가 성공해야 합니다.")
    check(len(attempts) == 2, "실패한 워커에 다시 붙지 말고 한 번씩만 시도해야 합니다.")
    status = {worker["id"]: worker for worker in pool.status()}
    check(status["llm-1"]["healthy"] is False, "실패한 워커는 비정상으로 표시돼야 합니다.")
    check(status["llm-1"]["failed"] == 1, "실패 횟수를 세야 합니다.")
    check(status["llm-2"]["completed"] == 1, "성공 횟수를 세야 합니다.")
    check(pool.total_capacity == 1, "용량 합계는 건강한 워커만 세야 합니다(ETA 가 부풀면 안 됩니다).")

    # 재시도 불가 오류는 그대로 올린다 — 다른 워커로 옮겨도 같은 결과다.
    pool = LlmWorkerPool(parse_worker_spec("http://a@1,http://b@1", "x"))
    calls = {"count": 0}

    async def bad_request(worker):
        calls["count"] += 1
        raise http_error(400)

    try:
        await pool.run(bad_request)
        failures.append("400 은 예외로 올라와야 합니다.")
    except httpx.HTTPStatusError:
        pass
    check(calls["count"] == 1, "재시도 불가 오류를 다른 워커에서 반복하면 안 됩니다.")

    # 모든 워커가 죽어도 무한 대기하지 않고 마지막 오류를 올린다.
    pool = LlmWorkerPool(parse_worker_spec("http://a@1,http://b@1", "x"))

    async def always_dead(worker):
        raise httpx.ConnectError("refused", request=request)

    try:
        await asyncio.wait_for(pool.run(always_dead), timeout=3)
        failures.append("전부 죽으면 예외가 올라와야 합니다.")
    except httpx.ConnectError:
        pass
    except asyncio.TimeoutError:
        failures.append("전부 죽었을 때 무한 대기하면 안 됩니다.")


asyncio.run(main())

if failures:
    for failure in failures:
        print(f"- FAIL {failure}", file=sys.stderr)
    print(f"test_worker_pool: {len(failures)}건 실패", file=sys.stderr)
    sys.exit(1)

print("test_worker_pool: OK")
