"""다중 LLM 엔드포인트 부하 분산 — 원본 `lib/llm-worker-pool.js` 이식.

원본은 Node 이벤트 루프에서 waiter 배열 + drain 타이머로 돌았다. 여기서는 같은 정책을
asyncio.Condition 으로 옮겼다 — 대기자를 깨우는 방식만 다르고 선택 규칙·쿨다운·페일오버는 같다.

선택 규칙(원본과 동일): 여유율(inFlight/capacity)이 낮은 워커 → 최근에 덜 쓴 워커 → 설정 순서.
재시도 가능한 오류면 그 워커를 쿨다운시키고 **다른 워커로 옮겨** 재시도한다.
같은 워커로 재시도하지 않는 것이 핵심이다 — GPU 한 대가 죽었을 때 거기서만 계속 실패하면
전체 처리량이 0 이 된다.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone

import httpx

DEFAULT_COOLDOWN_MS = 30000

_SPEC_RE = re.compile(r"^(https?://[^@]+?)(?:@(\d+))?$", re.IGNORECASE)

# 원본 isRetryableLlmError 와 같은 판정.
_RETRYABLE_STATUS = {408, 409, 429}
_RETRYABLE_NETWORK = (
    httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
    httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError,
)


def parse_worker_spec(value: str | None, fallback_base: str) -> list[dict]:
    source = str(value or "").strip() or f"{fallback_base}@1"
    seen: set[str] = set()
    workers: list[dict] = []
    for index, part in enumerate(p.strip() for p in source.split(",")):
        if not part:
            continue
        match = _SPEC_RE.match(part)
        if not match:
            raise ValueError(f"잘못된 LLM 워커 설정: {part}")
        base = match.group(1).rstrip("/")
        if base in seen:
            raise ValueError(f"중복 LLM 워커: {base}")
        seen.add(base)
        capacity = min(max(int(match.group(2) or 1), 1), 32)
        workers.append({"id": f"llm-{len(workers) + 1}", "base": base, "capacity": capacity})
    if not workers:
        raise ValueError("LLM 워커가 하나 이상 필요합니다.")
    return workers


def is_retryable_llm_error(error: BaseException) -> bool:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", 0) or 0
    if status:
        return status in _RETRYABLE_STATUS or status >= 500
    return isinstance(error, _RETRYABLE_NETWORK)


class LlmWorkerPool:
    def __init__(self, workers: list[dict], cooldown_ms: int = DEFAULT_COOLDOWN_MS):
        if not workers:
            raise ValueError("LLM 워커가 하나 이상 필요합니다.")
        self.workers = [
            {
                **worker,
                "order": order,
                "inFlight": 0,
                "healthy": True,
                "cooldownUntil": 0.0,
                "consecutiveFailures": 0,
                "completed": 0,
                "failed": 0,
                "model": None,
                "lastError": None,
                "lastCheckedAt": None,
                "sequence": 0,
            }
            for order, worker in enumerate(workers)
        ]
        self.cooldown_ms = cooldown_ms
        self.sequence = 0
        self._condition = asyncio.Condition()

    # ── 선택 ────────────────────────────────────────────────────────────────
    def _candidate(self, excluded: set[str]) -> dict | None:
        now = time.time() * 1000
        usable = [
            worker for worker in self.workers
            if worker["id"] not in excluded
            and worker["inFlight"] < worker["capacity"]
            and (worker["healthy"] or worker["cooldownUntil"] <= now)
        ]
        if not usable:
            return None
        usable.sort(key=lambda w: (w["inFlight"] / w["capacity"], w["sequence"], w["order"]))
        return usable[0]

    async def _acquire(self, excluded: set[str]) -> dict:
        async with self._condition:
            while True:
                worker = self._candidate(excluded)
                if worker is not None:
                    worker["inFlight"] += 1
                    self.sequence += 1
                    worker["sequence"] = self.sequence
                    return worker
                # 쿨다운이 걸려 있으면 그 시각에 맞춰 깨어난다(없으면 짧게 폴링).
                future = [w["cooldownUntil"] for w in self.workers if w["cooldownUntil"] > time.time() * 1000]
                delay = (min(future) - time.time() * 1000) / 1000 if future else 0.05
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=max(0.001, delay))
                except asyncio.TimeoutError:
                    pass

    async def _release(self, worker: dict, error: BaseException | None = None) -> None:
        async with self._condition:
            worker["inFlight"] = max(0, worker["inFlight"] - 1)
            if error is not None:
                worker["failed"] += 1
                worker["consecutiveFailures"] += 1
                worker["lastError"] = str(getattr(error, "message", None) or error)[:300]
                if is_retryable_llm_error(error):
                    worker["healthy"] = False
                    worker["cooldownUntil"] = time.time() * 1000 + self.cooldown_ms
            else:
                worker["completed"] += 1
                worker["consecutiveFailures"] = 0
                worker["lastError"] = None
                worker["healthy"] = True
                worker["cooldownUntil"] = 0.0
            self._condition.notify_all()

    async def run(self, task):
        """task(worker) 를 실행한다. 재시도 가능한 실패면 다른 워커로 옮겨 다시 시도한다."""
        attempted: set[str] = set()
        while True:
            worker = await self._acquire(attempted)
            attempted.add(worker["id"])
            try:
                result = await task(worker)
            except BaseException as error:  # noqa: BLE001 — 실패 기록 후 그대로 올린다
                await self._release(worker, error)
                remaining = any(candidate["id"] not in attempted for candidate in self.workers)
                if not remaining or not is_retryable_llm_error(error):
                    raise
            else:
                await self._release(worker)
                return result

    # ── 상태 ────────────────────────────────────────────────────────────────
    async def health_check(self, timeout: float = 4.0, headers: dict | None = None) -> list[dict]:
        headers = headers or {}

        async def check(worker: dict) -> None:
            last_error: BaseException | None = None
            async with httpx.AsyncClient(timeout=timeout) as client:
                for route in ("/api/v0/models", "/v1/models"):
                    try:
                        response = await client.get(f"{worker['base']}{route}", headers=headers)
                        response.raise_for_status()
                        data = response.json()
                        models = data.get("data") or data.get("models") or []
                        if route == "/api/v0/models":
                            loaded = next(
                                (m for m in models
                                 if m.get("state") == "loaded" and m.get("type") != "embeddings"),
                                None,
                            )
                        else:
                            loaded = models[0] if models else None
                        if not loaded:
                            last_error = RuntimeError("로드된 채팅 모델이 없습니다.")
                            continue
                        worker["model"] = loaded.get("id") or loaded.get("name") or loaded.get("model")
                        worker["healthy"] = True
                        worker["cooldownUntil"] = 0.0
                        worker["consecutiveFailures"] = 0
                        worker["lastError"] = None
                        worker["lastCheckedAt"] = datetime.now(timezone.utc).isoformat()
                        return
                    except BaseException as error:  # noqa: BLE001
                        last_error = error
                        status = getattr(getattr(error, "response", None), "status_code", None)
                        if status != 404:
                            break
            worker["healthy"] = False
            worker["lastError"] = str(last_error or "health check failed")[:300]
            worker["lastCheckedAt"] = datetime.now(timezone.utc).isoformat()

        await asyncio.gather(*(check(worker) for worker in self.workers))
        return self.status()

    def status(self) -> list[dict]:
        return [
            {
                "id": worker["id"],
                "base": worker["base"],
                "capacity": worker["capacity"],
                "inFlight": worker["inFlight"],
                "healthy": worker["healthy"],
                "model": worker["model"],
                "completed": worker["completed"],
                "failed": worker["failed"],
                "consecutiveFailures": worker["consecutiveFailures"],
                "lastError": worker["lastError"],
                "lastCheckedAt": worker["lastCheckedAt"],
            }
            for worker in self.workers
        ]

    @property
    def total_capacity(self) -> int:
        """건강한 워커의 동시 처리 용량 합. 내보내기 ETA 계산에 쓰인다."""
        return sum(worker["capacity"] for worker in self.workers if worker["healthy"])


_singleton: LlmWorkerPool | None = None
_singleton_key = ""


def get_llm_worker_pool(fallback_base: str) -> LlmWorkerPool:
    import os

    global _singleton, _singleton_key
    key = f"{os.getenv('LLM_WORKERS') or ''}|{fallback_base}"
    if _singleton is None or _singleton_key != key:
        _singleton = LlmWorkerPool(parse_worker_spec(os.getenv("LLM_WORKERS"), fallback_base))
        _singleton_key = key
    return _singleton
