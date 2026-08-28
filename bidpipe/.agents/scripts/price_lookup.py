# -*- coding: utf-8 -*-
"""가격 DB 조회 — 웹 검색 전에 먼저 여기를 본다. LLM 호출 0.

  python price_lookup.py 9100 PRO 2TB      이름/스펙/모델코드 검색
  python price_lookup.py --key danawa:103413458
  python price_lookup.py --stale           신선도 만료 목록 (재조사 대상)
  python price_lookup.py --tier D          특정 티어만 (D = 투찰 근거로 쓰면 안 되는 것)
  python price_lookup.py --history danawa:94083122   그 상품의 관측 이력 전체
  python price_lookup.py --anchor Xeon 4510          가격 미공개 품목의 **앵커** 찾기

가격이 안 잡히는 품목(서버 베어본·서버 CPU·엔터프라이즈 스토리지)은 --anchor 를 먼저 본다.
올서버 카탈로그(실판매가)와 조달 종합쇼핑몰(계약단가=상한)을 같이 됀진다.
티어 옆의 ≤ 표시는 bound=upper(상한일 뿐 그 값이 아님)를 뜻한다.

출력의 티어와 경과일을 반드시 같이 읽을 것:
  A 당사 매입가표/견적서   그대로 원가로 써도 된다
  B 판매처 상품 페이지 실측  재현 가능. 마감 임박 건은 재확인 권장
  C 다나와 최저가 스냅샷    판매처·금액이 수시로 바뀐다. 투찰 전 반드시 재확인
  D 추정/환율환산/출처불명   **투찰 근거 금지.** 실판매가를 새로 조사할 것
"""
import os, sys, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from price_schema import VIEW, load_products, iter_obs, infer_tier, TIER_DESC

FRESH_DAYS = {"high": 3, "normal": 14}


def load_view():
    v = json.load(open(VIEW, encoding="utf-8"))
    return v["items"] if isinstance(v, dict) else v


def age_days(d):
    try:
        return (datetime.date.today() - datetime.date(*map(int, d[:10].split("-")))).days
    except Exception:
        return 999


def fmt(r):
    a = age_days(r.get("date", ""))
    lim = FRESH_DAYS.get(r.get("volatility", "normal"), 14)
    flag = "  ⚠재조사" if a > lim else ("  ·스팟확인" if a > lim // 2 else "")
    price = r.get("price") or 0
    line = "[%s] %-40s %12s원  %s  %s일전%s" % (
        r.get("tier", "?") + ("≤" if r.get("bound") == "upper" else ""),
        (r.get("name") or "")[:40], format(price, ","),
        (r.get("vendor") or "-")[:18], a, flag)
    sub = "     %s" % (r.get("spec") or "")[:96]
    url = "     %s" % (r.get("url") or "(URL 없음)")
    return "\n".join([line, sub, url])


SNAPS = [("올서버", "allserver_catalog.json", "point"),
         ("조달몰", "g2b_shop_catalog.json", "upper")]


def anchors(terms, limit=30):
    """가격이 공개 안 되는 품목의 앵커를 스냅샷에서 찾는다.
    조달몰 계약단가는 **상한**이다. 원가 행에 그대로 넣으면 안 된다."""
    from price_schema import DATA
    import re as _re
    # 숫자/모델코드는 단어 경계로 맞춰야 한다.
    # 그냥 부분문자열로 보면 '4510'이 'ITA176-S4516320'에 걸려 쓰레기가 섮인다
    pats = []
    for t in terms:
        t = t.lower()
        pats.append(_re.compile(r"(?<![0-9a-z])%s(?![0-9a-z])" % _re.escape(t))
                    if _re.fullmatch(r"[0-9a-z\-]+", t) else None)
    toks = [t.lower() for t in terms]
    for label, fn, bound in SNAPS:
        path = os.path.join(DATA, fn)
        if not os.path.exists(path):
            print("[%s] 스냅샷 없음 (%s)" % (label, fn))
            continue
        items = json.load(open(path, encoding="utf-8")).get("items", [])

        def _ok(i):
            blob = json.dumps(i, ensure_ascii=False).lower()
            return all(p.search(blob) if p else (t in blob)
                       for p, t in zip(pats, toks))

        hits = [i for i in items if i.get("price") and _ok(i)]
        hits.sort(key=lambda i: i["price"])
        tag = "계약단가 = 상한(bound=upper)" if bound == "upper" else "실판매가"
        print("\n== [%s] %d건  — %s ==" % (label, len(hits), tag))
        for i in hits[:limit]:
            print("  %12s원  %s" % (format(i["price"], ","), (i.get("name") or "")[:74]))
            spec = i.get("spec") or json.dumps(i.get("attrs", {}), ensure_ascii=False)
            if spec and spec != "{}":
                print("                %s" % spec[:130])
            print("                %s" % i.get("url"))


def main():
    args = sys.argv[1:]

    if "--anchor" in args:
        anchors([a for a in args[args.index("--anchor") + 1:] if not a.startswith("--")])
        return

    rows = load_view()

    if "--history" in args:
        key = args[args.index("--history") + 1]
        hist = [o for o in iter_obs() if o.get("key") == key]
        p = load_products().get(key, {})
        print("%s  %s" % (key, p.get("name", "")))
        print("관측 %d건:" % len(hist))
        for o in sorted(hist, key=lambda x: x.get("observed_at") or ""):
            # 티어는 '판정'이라 규칙이 바뀌면 결과도 바뀐다. 뷰와 어긋나지 않도록 현행 규칙으로 다시 매긴다.
            t_now = infer_tier(o.get("vendor"), o.get("url"), o.get("method"),
                               valid_until=o.get("valid_until"),
                               quote_source=o.get("quote_source"),
                               evidence_urls=o.get("evidence_urls"))
            print("  %s  [%s] %12s원  %-16s %-10s %s" % (
                (o.get("observed_at") or "")[:16], t_now,
                format(o.get("price") or 0, ","), (o.get("vendor") or "-")[:16],
                o.get("method", ""), o.get("observer", "")))
        return

    if "--stale" in args:
        sel = [r for r in rows
               if age_days(r.get("date", "")) > FRESH_DAYS.get(r.get("volatility", "normal"), 14)]
        print("신선도 만료 %d건 (전체 %d건 중) — 재조사 대상\n" % (len(sel), len(rows)))
        rows = sorted(sel, key=lambda r: -age_days(r.get("date", "")))
    elif "--tier" in args:
        t = args[args.index("--tier") + 1].upper()
        rows = [r for r in rows if r.get("tier") == t]
        print("티어 %s: %s — %d건\n" % (t, TIER_DESC.get(t, ""), len(rows)))
    elif "--key" in args:
        k = args[args.index("--key") + 1]
        rows = [r for r in rows if r.get("key") == k]
    else:
        q = " ".join(a for a in args if not a.startswith("--")).strip().lower()
        if not q:
            print(__doc__)
            from collections import Counter
            c = Counter(r.get("tier") for r in rows)
            print("현재 %d상품 | 티어 %s" % (len(rows), dict(sorted(c.items()))))
            return
        rows = [r for r in rows
                if q in ((r.get("name") or "") + " " + (r.get("spec") or "") + " " +
                         (r.get("key") or "")).lower()]
        print("'%s' 검색결과 %d건\n" % (q, len(rows)))

    for r in sorted(rows, key=lambda r: (r.get("tier") or "Z", r.get("name") or ""))[:40]:
        print(fmt(r))
        print()


if __name__ == "__main__":
    main()
