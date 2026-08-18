"""브라우저 폴백 — 다나와가 httpx 스크래핑을 막을 때만 무겁게 뜬다.

평시 경로는 httpx(빠름, 페이지당 수백 ms)고, **차단 신호를 감지했을 때만**
헤드리스 크로미움으로 한 번 재시도한다. 브라우저는 지문이 실제 브라우저라 우회
확률이 높지만 페이지당 수 초가 걸리고 메모리를 크게 쓴다 — 기본 경로로 삼지 않는다.

설치(선택):
    .venv/bin/pip install playwright && .venv/bin/playwright install chromium
없으면 이 모듈은 조용히 비활성된다 — `fetch_html` 이 예외를 올리고 호출부가
평소와 같은 PRICE_SOURCE_BROKEN 으로 분류한다(치명적이지 않다).

브라우저는 **한 번만 띄워 상주**시킨다 — 요청마다 띄우면 페이지보다 런치가 비싸다.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlencode

#: 차단·봇 검증 페이지 신호. 다나와는 캡차/비정상 접근 안내를 이 꼴로 낸다.
_BLOCK_SIGNALS = re.compile(
    r"캡차|captcha|비정상|접근이\s*차단|차단된|blocked|forbidden|일시적인\s*오류", re.IGNORECASE)

#: 정상 목록·검색 페이지는 수백 KB — 차단 안내 페이지는 작고 상품 블록이 없다.
_SMALL_PAGE_CHARS = 5000

#: 실제 브라우저 UA — 헤드리스 감지가 UA 만 보고 거르는 일을 줄인다.
_BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")

_playwright = None
_browser = None


def enabled() -> bool:
    """브라우저 폴백을 켤지. `BROWSER_FALLBACK=0` 이면 항상 꺼짐."""
    return os.getenv("BROWSER_FALLBACK", "1") != "0"


def looks_blocked(html: str, marker: str = "productItem") -> bool:
    """응답 HTML 이 차단/봇 검증 페이지인가. 평시 경로의 폴백 트리거다.

    두 신호를 본다: (1) 차단 문구, (2) 상품 블록이 없는데 페이지가 비정상적으로
    작다 — 진짜 "검색 결과 없음" 페이지도 사이트 껍데기가 수십 KB 라서 구분된다.
    """
    if _BLOCK_SIGNALS.search(str(html or "")):
        return True
    if marker and marker not in html and len(str(html or "")) < _SMALL_PAGE_CHARS:
        return True
    return False


async def _get_browser():
    """헤드리스 크로미움을 한 번 띄우고 상주시킨다. 없으면 그 자리에서 실패한다."""
    global _playwright, _browser
    if _browser is None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise RuntimeError("playwright 미설치 — 브라우저 폴백을 못 쓴다") from error
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
    return _browser


async def fetch_html(url: str, params: dict | None = None, deadline_s: float = 20.0) -> str:
    """브라우저로 페이지를 열어 렌더링된 HTML 을 돌려준다. 폴백 전용 — 평시에 부르지 않는다."""
    if not enabled():
        raise RuntimeError("BROWSER_FALLBACK=0")
    target = f"{url}?{urlencode(params)}" if params else url
    browser = await _get_browser()
    context = await browser.new_context(user_agent=_BROWSER_UA, locale="ko-KR")
    page = await context.new_page()
    try:
        await page.goto(target, timeout=deadline_s * 1000, wait_until="domcontentloaded")
        return await page.content()
    finally:
        await context.close()


async def close() -> None:
    """상주 브라우저를 닫는다. 서비스 종료 경로에서 부른다."""
    global _browser, _playwright
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:  # noqa: BLE001 — 이미 닫힌 브라우저는 무시
            pass
        _browser = None
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        _playwright = None
