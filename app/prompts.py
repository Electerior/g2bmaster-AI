"""프롬프트 버전.

원본 `lib/analysis-history.js` 의 `ITEM_SUMMARY_PROMPT_VERSION` 이다.
이 값은 **분석 결과 재사용 키의 일부**다(`analysis_history.prompt_version`).

백엔드는 이 값을 하드코딩하지 않고 `GET /api/ai/prompt-version` 으로 읽어 간다.
프롬프트를 고쳤는데 이 상수를 그대로 두면 낡은 분석 결과가 계속 재사용된다 —
**프롬프트를 수정하면 반드시 이 값을 함께 올린다.**

기존 캐시와의 호환을 위해 원본 값을 그대로 유지한다. 프롬프트 본문을 옮겨오기 전까지는
버전을 올리지 않는다(올리면 이관 시점에 기존 분석이 전부 무효가 된다).
"""

ITEM_SUMMARY_PROMPT_VERSION = "item-summary-2026-08-04-v4"
