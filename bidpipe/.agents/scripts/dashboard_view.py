# -*- coding: utf-8 -*-
"""마진율 '표기' 규칙의 단일 기준 (AGENTS.md 규칙 6).

계산은 audit_prices.py가, **표기는 여기가** 유일하게 한다.

원래 이 로직은 `dashboard/web/format.ts`에만 있었다. 그 상태로 구글시트 미러를 붙이면
같은 규칙이 TS와 파이썬 두 곳에 살게 되고, 규칙 6이 바뀔 때 한쪽만 고쳐져 조용히 갈라진다.
(대시보드가 자체 계산식을 갖지 않게 한 것과 같은 이유다 — README '무엇을 읽나' 참조)

  scan_dashboard.py  → 각 행에 `view`를 실어 보낸다
  web/format.ts      → 그 `view`를 그대로 표시만 한다 (재계산 금지)
  sheet_export.py    → 그 `view`를 그대로 시트에 쓴다

D-day는 여기 없다. '지금'에 의존하는 값이라 스캔 시점에 굳으면 캐시된 목록에서 틀린다.
프론트는 매 렌더마다, 시트 내보내기는 푸시 시점에 각자 계산한다 (`dday_label`).
"""
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP

# audit_prices.EST_SHARE_BLOCK 과 짝. import 순환을 피하려고 인자로 받는다.

GOOD, MID = 20.0, 10.0          # 마진율 색 구분 기준


def fixed(x, d=1):
    """JS Number.toFixed 와 같은 반올림(절반은 0에서 먼 쪽)."""
    if x is None:
        return "-"
    q = Decimal(1).scaleb(-d) if d else Decimal(1)
    return str(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))


def pct(x, d=1):
    return "-" if x is None else f"{fixed(x, d)}%"


def margin_view(rec, block):
    """마진율 한 칸에 무엇을 쓸지. web/format.ts marginView 와 1:1.

    반환: {text, cls, note, blocked, sort}
      sort = 정렬에 쓸 수치. **보고 금지 건은 None**이다.
             수치를 가려도 행의 위치가 그 수치를 알려주기 때문에,
             가린 값으로 줄을 세우는 것 자체가 보고다 (규칙 6).
    """
    status = rec.get("status")
    rate = rec.get("margin_rate")

    if status == "no_cost":
        return dict(text="원가 미입력", cls="muted",
                    note="물품 행이 없거나 견적이 전부 0원", blocked=True, sort=None)
    if status == "no_margin" or rate is None:
        return dict(text="판정 불가", cls="muted",
                    note=rec.get("error") or "기초금액·물품 미기재", blocked=True, sort=None)

    share = rec.get("est_share") or 0.0
    if share >= block:
        return dict(text="판정보류", cls="muted",
                    note=f"추정 비중 {pct(share, 0)} ≥ {fixed(block, 0)}% — 견적 확보 전 보고 금지",
                    blocked=True, sort=None)

    # (2026-08-21) 견적 미확보를 0원으로 넣은 행이 있으면 총원가는 '하한'이라
    # 마진율은 상한값일 뿐 마진율이 아니다. 이전엔 이 건이 아래 일반 분기로 떨어져
    # blocked=True인데도 **초록 96.4%로 표시되고 정렬값까지 살아있었다**
    # (송원대 R26BK01686155: 선택 4행 중 3행이 0원 견적 → 원가 378만원 → 목록 최상단).
    # 수치를 가려도 행의 위치가 그 수치를 알려주므로 sort까지 죽인다 (위 docstring).
    zq = rec.get("zero_quote_rows") or 0
    if zq:
        return dict(text="원가 미확정", cls="muted",
                    note=f"견적 미확보 {zq}행을 0원으로 계상 — 원가는 하한, 마진율은 상한값이라 보고 금지",
                    blocked=True, sort=None)

    notes = []
    if share > 0:
        notes.append(f"잠정 · 추정 {rec.get('est_rows') or 0}행 {pct(share, 0)}")
    if rec.get("zero_quote_rows"):
        notes.append(f"↓ 견적0원 {rec['zero_quote_rows']}행")
    if rec.get("bypass_rows"):
        notes.append(f"추정가우회 {rec['bypass_rows']}행")

    return dict(
        text=f"{fixed(rate, 1)}%{'*' if share > 0 else ''}",
        cls="good" if rate >= GOOD else ("mid" if rate >= MID else "bad"),
        note=" · ".join(notes),
        blocked=not rec.get("reportable"),
        # 정렬 키는 status=='ok' 이고 추정 비중이 임계 미만일 때만 살아 있다
        sort=rate if status == "ok" else None,
    )


def dday_label(deadline_iso, now=None):
    """마감까지 남은 일수. 48시간 이내는 임박. web/format.ts dday 와 같은 기준."""
    if not deadline_iso:
        return ""
    try:
        t = dt.datetime.fromisoformat(deadline_iso)
    except (TypeError, ValueError):
        return ""
    hours = (t - (now or dt.datetime.now())).total_seconds() / 3600
    if hours < 0:
        return "마감"
    days = int(hours // 24)
    if hours <= 48:
        return f"D-0 ({int(hours)}h)" if days <= 0 else "D-1"
    return f"D-{days}"


def integrity_label(rec):
    """목록으로 볼 때만 드러나는 사고 (규칙 4)."""
    flags = []
    if rec.get("identical_to"):
        flags.append(f"중복파일({len(rec['identical_to'])})")
    if rec.get("dup_notice"):
        flags.append(f"공고중복({len(rec['dup_notice'])})")
    if rec.get("name_mismatch"):
        flags.append("파일명불일치")
    return " · ".join(flags)
