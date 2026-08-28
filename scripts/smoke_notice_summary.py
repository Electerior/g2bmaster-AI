#!/usr/bin/env python3
"""공고 요약 연기 테스트 — 실제 LLM 에 붙여 `POST /api/notice-summary` 를 돌린다.

계약 테스트(`test_http_contract.py`)는 환경에 흔들리면 안 되므로 닫힌 주소를 박아 두고
"LLM 이 없을 때 200 을 내지 않는가"만 본다. **요약이 실제로 쓸 만한가**는 여기서 본다 —
`smoke_llm.py` 가 LLM 연결을 따로 확인하는 것과 같은 자리다. DoD(`make check`)에는
포함되지 않는다. LLM 이 필요하기 때문이다.

라우트를 그대로 태운다(핸들러 직접 호출이 아니다). 그래야 실패했을 때 백엔드가 실제로
받게 될 본문 모양(`code`·`retryable`·`requestId`)까지 같이 확인된다.

    make notice                                          # 픽스처 3건 전부
    python scripts/smoke_notice_summary.py test/fixtures/notices/001-*.json
    python scripts/smoke_notice_summary.py --base http://127.0.0.1:8000   # 뜬 서버로
    LMS_BASE=http://gpu:1234 make notice

공고 JSON 모양(`test/fixtures/notices/*.json` 과 같다):

    {"bidNtceNo": "...", "title": "...", "agency": "...", "amount": 0,
     "documents": [{"name": "규격서.hwp", "text": "..."}]}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 임베딩 가중치는 요약과 무관하다. 예열하면 첫 호출이 그만큼 늦어져 측정이 흐려진다.
os.environ.setdefault("EMBEDDING_WARMUP", "0")

import httpx  # noqa: E402

from app.llm.client import host, llm_status  # noqa: E402
from app.prompts import NOTICE_SUMMARY_PROMPT_VERSION  # noqa: E402

FIXTURES = ROOT / "test" / "fixtures" / "notices"


def load_notices(paths: list[str]) -> list[tuple[str, dict]]:
    """인자로 받은 JSON 들, 없으면 픽스처 전부."""
    files = [pathlib.Path(p) for p in paths] if paths else sorted(FIXTURES.glob("*.json"))
    notices = []
    for path in files:
        if not path.is_file():
            print(f"  건너뜀 — 파일이 없습니다: {path}", file=sys.stderr)
            continue
        try:
            notices.append((path.name, json.loads(path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            print(f"  건너뜀 — JSON 을 읽지 못했습니다({path}): {error}", file=sys.stderr)
    return notices


def post_notice(base: str | None, payload: dict, timeout: float) -> tuple[int, dict]:
    """(status, body). base 가 없으면 서버 없이 앱을 직접 태운다."""
    if base:
        with httpx.Client(base_url=base, timeout=timeout) as client:
            response = client.post("/api/notice-summary", json=payload)
    else:
        from fastapi.testclient import TestClient

        from app.main import app

        # 컨텍스트 매니저로 열지 않는다 — startup 이벤트(임베딩 예열)를 태우지 않기 위해서다.
        response = TestClient(app).post("/api/notice-summary", json=payload)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"error": response.text[:200]}


def report(name: str, status: int, body: dict, elapsed: float) -> tuple[bool, int]:
    """한 건을 출력하고 (성공 여부, 요약 길이) 를 돌려준다."""
    print(f"\n── {name} " + "─" * max(0, 60 - len(name)))

    if status != 200:
        code = body.get("code", "?")
        print(f"  HTTP {status}  code={code}  retryable={body.get('retryable')}")
        print(f"  detail: {str(body.get('detail') or body.get('error') or '')[:200]}")
        return False, 0

    summary = (body.get("summary") or "").strip()
    version = body.get("promptVersion")
    print(f"  HTTP 200  {elapsed:.1f}s  {len(summary)}자  model={body.get('llmModel')}")

    ok = True
    if not summary:
        # 빈 요약이 200 으로 나오면 호출부는 "요약이 없는 공고"로 오해한다.
        print("  FAIL — 요약이 비어 있는데 200 이다", file=sys.stderr)
        ok = False
    if version != NOTICE_SUMMARY_PROMPT_VERSION:
        # 버전이 어긋나면 백엔드 재사용 키가 깨진다. 프롬프트만 고치고 버전을 안 올린 경우다.
        print(f"  FAIL — promptVersion 불일치: {version} != {NOTICE_SUMMARY_PROMPT_VERSION}",
              file=sys.stderr)
        ok = False

    print()
    for line in summary.splitlines():
        print(f"  │ {line}")
    return ok, len(summary)


async def main() -> int:
    parser = argparse.ArgumentParser(description="공고 요약 연기 테스트")
    parser.add_argument("notices", nargs="*", help="공고 JSON 경로 (없으면 픽스처 전부)")
    parser.add_argument("--base", help="뜬 AI 서비스 주소. 없으면 서버 없이 앱을 직접 태운다")
    parser.add_argument("--timeout", type=float, default=180.0, help="한 건당 상한(초)")
    args = parser.parse_args()

    status = await llm_status()
    print(f"LLM base   : {host()}")
    print(f"reachable  : {status['reachable']}  model={status['loadedModel'] or '-'}")
    print(f"프롬프트   : {NOTICE_SUMMARY_PROMPT_VERSION}")
    print(f"경유       : {args.base or 'in-process (서버 없이)'}")

    # 라이브 확인이므로 LLM 이 없으면 실패가 아니라 SKIP 이다(smoke_llm.py 와 같은 규약).
    if not status["reachable"]:
        print("\nsmoke_notice_summary: SKIP — 도달 가능한 LLM 워커가 없습니다.")
        return 0
    if not status["loadedModel"]:
        print("\nsmoke_notice_summary: SKIP — 로드된 모델이 없습니다(`lms load <model>`).")
        return 0

    notices = load_notices(args.notices)
    if not notices:
        print(f"\nsmoke_notice_summary: FAIL — 읽을 공고가 없습니다({FIXTURES}).", file=sys.stderr)
        return 1

    rows, failed = [], 0
    for name, payload in notices:
        started = time.monotonic()
        try:
            code, body = post_notice(args.base, payload, args.timeout)
        except httpx.HTTPError as error:
            print(f"\n── {name}\n  FAIL — 호출 자체가 실패했습니다: {error}", file=sys.stderr)
            failed += 1
            continue
        elapsed = time.monotonic() - started
        ok, length = report(name, code, body, elapsed)
        failed += 0 if ok else 1
        rows.append((name, code, length))

    # docs/summary-eval.md 는 회차마다 표 한 줄이 늘어야 한다. 그대로 붙일 수 있게 낸다.
    print("\n" + "─" * 62)
    print(f"docs/summary-eval.md 용 ({NOTICE_SUMMARY_PROMPT_VERSION})\n")
    print("| 픽스처 | 상태 | 길이 |")
    print("|---|---|---|")
    for name, code, length in rows:
        print(f"| {pathlib.Path(name).stem} | {code} | {length}자 |")

    if failed:
        print(f"\nsmoke_notice_summary: FAIL — {failed}건", file=sys.stderr)
        return 1
    print(f"\nsmoke_notice_summary: OK — {len(rows)}건")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
