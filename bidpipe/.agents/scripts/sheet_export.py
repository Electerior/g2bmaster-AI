# -*- coding: utf-8 -*-
"""대시보드 목록을 구글시트에 붙일 매트릭스로 내보낸다.

    .agents/py .agents/scripts/sheet_export.py -o /tmp/sheet.json
    .agents/py .agents/scripts/sheet_export.py --tsv          # 눈으로 확인용

**표기는 여기서 정하지 않는다.** scan_dashboard.py가 실어 보낸 `view`(= dashboard_view.py)를
그대로 옮길 뿐이다. 시트가 자기 마진율 문자열을 만들면 대시보드 화면과 시트가 서로 다른 말을
하게 되고, 규칙 6이 한쪽에서만 지켜진다.

정렬 컬럼(`정렬키`)에 주의: **보고 금지 건은 빈 칸**이다. 시트에서 그 열로 정렬하면
판정보류·원가 미입력 건이 아래로 떨어진다. 실제 마진율을 몰래 넣어두면 화면에서 수치를
가려도 행의 위치가 그 수치를 알려준다 (규칙 6 — web/sort.ts의 keyOf와 같은 이유).

푸시는 파이썬이 못 한다. 이 프로젝트엔 구글 API 자격증명이 없어서, 에이전트가 브라우저
세션으로 `googleSheets.writeMatrix`를 호출한다. 이 스크립트는 그 재료만 만든다.
"""
import sys, os, json, argparse, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import scan_dashboard
import dashboard_view as dv

SHEET_DATA = "공고"
SHEET_META = "메타"

# (헤더, 값 뽑는 함수, 텍스트 강제). 컬럼을 여기 한 곳에서만 정의한다.
# text=True 는 시트가 값을 숫자로 삼키는 걸 막는다. '96.4%'를 그냥 붙여넣으면 0.964가 되고,
# 같은 컬럼의 '24.2%*'·'판정보류'는 문자열로 남아 한 컬럼에 두 종류가 섞인다.
COLUMNS = [
    ("마감",     lambda r, now: r.get("deadline") or "", True),
    ("D-day",   lambda r, now: dv.dday_label(r.get("deadline_iso"), now), True),
    ("분석일",   lambda r, now: _folder_date(r.get("folder")), False),
    ("수요기관", lambda r, now: r.get("agency") or "", False),
    ("공고명",   lambda r, now: r.get("title") or r.get("filename") or "", False),
    ("공고번호", lambda r, now: r.get("notice_no") or "", True),
    ("기초금액", lambda r, now: r.get("base"), False),
    ("투찰가",   lambda r, now: r.get("bid"), False),
    ("원가",     lambda r, now: r.get("cost"), False),
    ("마진율",   lambda r, now: r["view"]["text"], True),
    ("정렬키",   lambda r, now: r["view"]["sort"], False),
    ("가격근거", lambda r, now: _basis(r), False),
    ("비고",     lambda r, now: r["view"]["note"], False),
    ("무결성",   lambda r, now: r.get("integrity") or "", False),
    ("이율",     lambda r, now: _rate(r), True),
    ("낙찰방식", lambda r, now: r.get("award") or "", False),
    ("공고 URL", lambda r, now: r.get("url") or "", False),
    ("파일",     lambda r, now: r.get("file") or "", False),
    ("개요",     lambda r, now: (r.get("summary") or "")[:500], False),
]


def _folder_date(folder):
    return f"{folder[:4]}-{folder[4:6]}-{folder[6:8]}" if folder and len(folder) == 8 else (folder or "")


def _rate(r):
    rate = r.get("rate")
    if not rate:
        return ""
    basis = r.get("rate_basis") or ""
    return f"{dv.fixed(rate * 100, 1)}%" + (f" ({basis})" if basis else "")


def _basis(r):
    """NoticeRow의 '가격근거' 칩과 같은 정보. 마진율 옆에 항상 따라붙어야 한다 (규칙 6)."""
    share = r.get("est_share")
    out = []
    if share is not None:
        out.append("실가" if share == 0 else f"추정 {dv.fixed(share, 0)}%")
    if r.get("zero_quote_rows"):
        out.append(f"견적0원 {r['zero_quote_rows']}행")
    if r.get("bypass_rows"):
        out.append(f"추정가우회 {r['bypass_rows']}행")
    if r.get("unmet_rows"):
        out.append(f"규격확인 {r['unmet_rows']}행")
    if r.get("sel_rows"):
        out.append(f"선택 {r['sel_rows']}행")
    if r.get("rate_basis") == "기본값":
        out.append("하한 기본값")
    if not r.get("reportable"):
        out.append("보고금지")
    return " · ".join(out)


def _cell(v, force_text=False):
    """수치는 수치로, 문자열은 붙여넣기 사고가 안 나게 다듬어서.

    큰따옴표를 반드시 치운다. 클립보드 붙여넣기는 TSV이고, 시트는 `"`를 인용 구분자로
    읽어서 **닫히지 않은 따옴표 하나가 뒤쪽 셀·행을 통째로 삼킨다.**
    (2026-08-21 실측: 개요의 `모니터 24"` 한 글자 때문에 5행부터 컬럼이 밀려,
     마진율 칸에 공고명이 들어앉았다. 붙여넣기는 성공하고 값만 틀리니 보기 전엔 모른다)
    """
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "예" if v else ""
    if isinstance(v, (int, float)):
        return v
    s = str(v).replace("\t", " ").replace('"', "\u201d")   # " → ” (TSV 인용 붕괴 방지)
    s = " ".join(s.split())                       # 줄바꿈이 섞이면 붙여넣기에서 행이 밀린다
    if force_text or s[:1] in ("=", "+", "@"):     # 시트가 수식·숫자로 먹는 걸 막는다
        s = "'" + s
    return s


def build_matrix(payload, now=None):
    """데이터 시트. **1행이 바로 헤더**다.

    스캔 정보 배너를 1행에 두면 시트 필터가 그 행을 헤더로 잡아버린다
    (수동으로 범위를 지정해도 계속 되돌아갔다). 그래서 메타는 별도 시트로 뻐다.
    """
    now = now or dt.datetime.now()
    matrix = [[h for h, _, _ in COLUMNS]]
    for r in payload["rows"]:
        matrix.append([_cell(fn(r, now), t) for _, fn, t in COLUMNS])
    return matrix


def build_meta_matrix(payload, now=None):
    """스캔 시점·통계·무결성·표기 규칙. 시트만 보는 사람도 수치를 오해 안 하게 하는 장치다."""
    now = now or dt.datetime.now()
    rows = payload["rows"]
    iss = payload["issues"]
    block = dv.fixed(payload["est_share_block"], 0)

    hidden = [r for r in rows if r["view"]["sort"] is None]
    kinds = {}
    for r in hidden:
        kinds[r["view"]["text"]] = kinds.get(r["view"]["text"], 0) + 1
    shown = [r for r in rows if r["view"]["sort"] is not None and r.get("reportable")]

    m = [
        ["항목", "값", "설명"],
        ["스캔 시각", payload["generated_at"].replace("T", " "), "이 시각의 워크북 상태다. 이후 수정은 반영 안 됨"],
        ["내보낸 시각", now.strftime("%Y-%m-%d %H:%M:%S"), ""],
        ["분석 파일", payload["count"], "날짜 폴더(YYYYMMDD/)의 xlsx 전부"],
        ["보고 가능", len(shown), "전 품목 실판매가이거나 추정 비중이 임계 미만"],
        ["마진율 미표시", len(hidden), "'정렬키'가 비어 있어 정렬에서도 빠진다"],
        ["", "", ""],
        ["— 미표시 내역 —", "", ""],
    ]
    for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
        m.append([k, v, {
            "판정보류": f"원가의 {block}% 이상이 추정가 — 견적 확보 전 보고 금지",
            "원가 미입력": "물품 행이 없거나 견적이 전부 0원 (마진 100%가 아니다)",
            "판정 불가": "기초금액·물품이 없어 계산 자체가 안 된다",
        }.get(k, "")])

    m += [
        ["", "", ""],
        ["— 무결성 (목록으로 볼 때만 보이는 사고) —", "", ""],
        ["내용 동일 파일", iss["identical"], "바이트 단위로 같은 워크북이 두 곳에 있다"],
        ["공고번호 중복", iss["dup_notice"], "같은 공고를 두 번 분석했다"],
        ["파일명 불일치", iss["name_mismatch"], "파일명의 공고번호가 워크북 기재값(B5)과 다르다"],
        ["", "", ""],
        ["— 표기 규칙 (AGENTS.md 규칙 6) —", "", ""],
        ["마진율", "(투찰가 − 상품가격 합계) ÷ 투찰가", "부대비용은 원가에서 제외한다 (규칙 15)"],
        ["24.2%*", "추정가가 섞인 잠정치", "'가격근거'·'비고' 열에 추정 행수와 비중이 있다"],
        ["19.6% (별표 없음)", "전 행 실판매가", ""],
        ["정렬키", "보고 가능한 건만 값이 있다", "수치를 가려도 행의 위치가 그 수치를 알려주므로, 가린 값으로 줄을 세우지 않는다"],
        ["", "", ""],
        ["원본", payload["root"], "이 시트는 읽기 전용 미러다. 수정은 워크북에서 한다"],
    ]
    return [[_cell(c) for c in row] for row in m]


def check_matrix(matrix, label=""):
    """붙여넣기 전에 자가검증. 열 개수가 틀어지거나 구분자 문자가 남아있으면 잡는다."""
    width = len(matrix[0]) if matrix else 0
    problems = []
    for i, row in enumerate(matrix):
        if len(row) != width:
            problems.append(f"{label}{i + 1}행: 열 {len(row)}개 (기대 {width})")
        for j, c in enumerate(row):
            if isinstance(c, str) and any(ch in c for ch in ('\t', '\n', '\r', '"')):
                problems.append(f"{label}{i + 1}행 {j + 1}열: 구분자 문자 잔존")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", help="매트릭스 JSON 저장 경로")
    ap.add_argument("--tsv", action="store_true", help="TSV로 stdout에 (확인용)")
    ap.add_argument("--glob", default="20*/*.xlsx")
    args = ap.parse_args()

    payload = scan_dashboard.build_payload(args.glob)
    matrix = build_matrix(payload)
    meta = build_meta_matrix(payload)

    problems = check_matrix(matrix, "[공고]") + check_matrix(meta, "[메타]")
    if problems:
        for p in problems[:20]:
            print(f"[!] {p}", file=sys.stderr)
        print(f"[!] 붙여넣으면 행이 밀린다. 중단. ({len(problems)}건)", file=sys.stderr)
        sys.exit(1)

    if args.tsv:
        for row in matrix:
            print("\t".join("" if c == "" else str(c) for c in row))
        return

    out = {
        "generated_at": payload["generated_at"],
        "count": payload["count"],
        "sheets": [
            {"name": SHEET_DATA, "rows": len(matrix), "cols": len(COLUMNS), "matrix": matrix},
            {"name": SHEET_META, "rows": len(meta), "cols": 3, "matrix": meta},
        ],
    }
    text = json.dumps(out, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fp:
            fp.write(text)
        print(f"{payload['count']}건 · {SHEET_DATA} {len(matrix)}행×{len(COLUMNS)}열 · "
              f"{SHEET_META} {len(meta)}행 → {args.out}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
