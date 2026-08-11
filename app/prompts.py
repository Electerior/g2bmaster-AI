"""프롬프트 버전.

원본 `lib/analysis-history.js` 의 `ITEM_SUMMARY_PROMPT_VERSION` 이다.
이 값은 **분석 결과 재사용 키의 일부**다(`analysis_history.prompt_version`).

백엔드는 이 값을 하드코딩하지 말고 `GET /api/ai/prompt-version` 으로 읽어 간다.
프롬프트를 고쳤는데 이 상수를 그대로 두면 낡은 분석 결과가 계속 재사용된다 —
**프롬프트를 수정하면 반드시 이 값을 함께 올린다.**

기존 캐시와의 호환을 위해 원본 값을 그대로 유지한다. 프롬프트 본문을 옮겨오기 전까지는
버전을 올리지 않는다(올리면 이관 시점에 기존 분석이 전부 무효가 된다).
"""

ITEM_SUMMARY_PROMPT_VERSION = "item-summary-2026-08-04-v4"
BID_SUMMARY_PROMPT_VERSION = "bid-summary-2026-08-07-v1"

#: 사업기회 분류(`app/opportunity.py`). **아직 엔드포인트가 아니다** — 그래서 아래
#: PROMPT_VERSIONS 에 넣지 않는다. 그 맵은 백엔드가 재사용 키를 만드는 데 쓰는
#: '엔드포인트별' 표라, 엔드포인트가 아닌 것을 끼워 넣으면 없는 표면이 있는 것처럼 보인다.
#: HTTP 로 열게 되면 그때 옮긴다.
OPPORTUNITY_PROMPT_VERSION = "opportunity-2026-08-07-v2"

#: 엔드포인트별 프롬프트 버전. 단일 문자열은 `bid-summary` 가 들어오는 순간 깨지고,
#: 업무구분(물품·용역·공사·외자)별로 프롬프트가 갈리면 더 깨진다 — 키는 결국
#: `엔드포인트:문서종류` 가 된다(`decisions.md F-3`, `plan.md §3`).
#:
#: 아직 옮긴 프롬프트가 없어 모든 항목을 적을 수 없다. **없는 버전을 빈 문자열로 채우지 않는다** —
#: 백엔드가 그것을 재사용 키에 넣으면 서로 다른 상태가 같은 키로 묶인다.
PROMPT_VERSIONS: dict[str, str] = {
    "item-summary": ITEM_SUMMARY_PROMPT_VERSION,
    "bid-summary": BID_SUMMARY_PROMPT_VERSION,
}