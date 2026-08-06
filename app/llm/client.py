"""LLM(LM Studio 등 OpenAI 호환) 연동 — 원본 `lib/lms.js` 이식.

주소·모델·키는 ai-config(파일 > env)에서 읽어 설정 변경을 즉시 반영한다.
LM Studio 면 `/api/v0/models` 로 로드된 모델을 추적(TTL 10초)하고,
외부 API 면 404 가 나므로 config 의 llmModel 로 폴백한다.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path

import httpx

from ..config import get_ai_config, llm_headers
from .worker_pool import get_llm_worker_pool

TTL_SECONDS = 10.0

_cache = {"model": "", "context": 0, "ts": 0.0}


def host() -> str:
    return get_ai_config()["llmBase"]


async def loaded_model() -> str:
    if _cache["model"] and time.time() - _cache["ts"] < TTL_SECONDS:
        return _cache["model"]
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(f"{host()}/api/v0/models", headers=llm_headers())
            response.raise_for_status()
            data = response.json()
        model = next(
            (m for m in (data.get("data") or [])
             if m.get("state") == "loaded" and m.get("type") != "embeddings"),
            None,
        )
        # loaded_context_length 는 "지금 로드된" 컨텍스트다(모델 최대치와 다르다).
        # 프롬프트를 여기 맞춰 잘라야 한다 — 넘기면 LM Studio 가 400 을 돌려주고,
        # 그 400 이 화면에서는 "LLM 서버에 연결하지 못해"로 둔갑한다.
        if model and model.get("id"):
            _cache.update(model=model["id"], context=int(model.get("loaded_context_length") or 0), ts=time.time())
            return model["id"]
    except BaseException:  # noqa: BLE001 — LMS 미기동·외부 API 등은 폴백이 정상 경로다
        pass
    return _cache["model"] or get_ai_config().get("llmModel") or ""


async def loaded_context() -> int:
    """지금 로드된 컨텍스트 토큰 수. 모르면 0(호출부는 기존 상한을 그대로 쓴다)."""
    await loaded_model()
    return _cache["context"] or 0


def models_from_disk() -> list[dict]:
    """LM Studio 서버가 꺼져 있어도 설치된 모델은 디스크에 있다.

    HTTP 조회 실패를 "모델 없음"으로 보여 주면 사용자가 고를 수가 없어 폴더를 직접 훑는다.
    임베딩(All-MiniLM 등)과 mmproj(비전 보조 가중치)는 채팅에 못 쓰므로 뺀다.
    """
    root = Path(os.getenv("LMS_MODELS_DIR") or (Path.home() / ".lmstudio" / "models"))
    out: list[dict] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > 3:
            return
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        ggufs = [e for e in entries if e.is_file() and e.name.endswith(".gguf")]
        if ggufs:
            usable = any(not re.match(r"^mmproj", e.name, re.IGNORECASE) for e in ggufs)
            identifier = str(directory.relative_to(root)).replace("\\", "/")
            if usable and identifier and not re.search(r"embed|MiniLM", identifier, re.IGNORECASE):
                out.append({"id": identifier, "state": None})
            return   # 모델 폴더 안쪽은 더 들어가지 않는다
        for entry in entries:
            if entry.is_dir():
                walk(entry, depth + 1)

    if root.exists():
        walk(root, 0)
    return out


async def available_models() -> dict:
    for route, pick in (
        ("/api/v0/models", lambda d: [m for m in (d.get("data") or []) if m.get("type") != "embeddings"]),
        ("/v1/models", lambda d: d.get("data") or []),
    ):
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                response = await client.get(f"{host()}{route}", headers=llm_headers())
                response.raise_for_status()
                data = response.json()
            models = [
                {"id": m.get("id"), "state": m.get("state")}
                for m in pick(data) if m.get("id")
            ]
            if models:
                return {"ok": True, "source": "server", "models": models}
        except BaseException:  # noqa: BLE001 — 다음 경로를 시도한다
            continue
    # 서버에는 못 붙었지만 설치된 모델은 보여 준다(ok=False 로 상태는 정직하게 알린다).
    return {"ok": False, "source": "filesystem", "models": models_from_disk()}


async def llm_status() -> dict:
    """실제 도달 여부 판정.

    loaded_model() 은 서버가 없어도 config 값을 돌려주므로 그것으로 "붙었는지"를
    판단하면 안 된다 — 꺼져 있는데 로드된 것처럼 보고된 적이 있다.
    """
    models_result = await available_models()
    workers = await get_llm_worker_pool(host()).health_check(headers=llm_headers())
    models = models_result["models"]
    loaded = next((m for m in models if m.get("state") == "loaded"), None)
    healthy = next((w for w in workers if w["healthy"]), None)
    return {
        "reachable": any(w["healthy"] for w in workers),
        "base": host(),
        "loadedModel": (loaded or {}).get("id") or (healthy or {}).get("model"),
        "availableCount": len(models),
        "workers": workers,
    }


async def llm_worker_status() -> list[dict]:
    return await get_llm_worker_pool(host()).health_check(headers=llm_headers())


async def lms_chat(request: dict) -> dict:
    """OpenAI 호환 채팅 완성. 워커 풀이 분배·페일오버를 맡는다."""
    request_id = uuid.uuid4().hex[:8]
    headers = {**llm_headers(), "x-request-id": request_id}

    async def call(worker: dict) -> dict:
        body = {
            **request,
            "reasoning_effort": "none",
            "chat_template_kwargs": {
                **(request.get("chat_template_kwargs") or {}),
                "enable_thinking": False,
            },
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(f"{worker['base']}/v1/chat/completions", json=body, headers=headers)
            if response.is_error:
                # 원본 주석이 지적한 함정: LM Studio 가 돌려주는 400(컨텍스트 초과, 모델 미로드)이
                # 그냥 "HTTP 400" 으로 올라가면 화면에는 "LLM 서버에 연결하지 못해"로 둔갑한다.
                # 서버가 알려 준 사유를 예외 메시지에 실어야 원인을 볼 수 있다.
                raise httpx.HTTPStatusError(
                    f"LLM {response.status_code}: {server_reason(response)}",
                    request=response.request,
                    response=response,
                )
            return response.json()

    return await get_llm_worker_pool(host()).run(call)


def server_reason(response: httpx.Response) -> str:
    """OpenAI 호환 오류 본문에서 사람이 읽을 사유를 뽑는다."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:300]
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    if isinstance(error, str):
        return error[:300]
    return str(data)[:300]
