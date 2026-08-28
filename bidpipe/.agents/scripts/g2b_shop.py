# -*- coding: utf-8 -*-
"""조달청 나라장터 종합쇼핑몰 계약단가 수집기 (LLM 0, 로그인 0).

왜 필요한가 (2026-08-20)
  서버 베어본처럼 시중에 가격이 안 걸리는 품목은 "견적 필요"로 넘기면 그대로 화석이 된다.
  종합쇼핑몰에는 **같은 급 장비의 조달 계약단가가 공개**돼 있고, 로그인 없이 긁힌다.

  이 값을 원가로 쓰면 안 된다. 계약단가는 소매가보다 비싸다. 대신 세 가지를 준다.
    1) 수요기관이 예산을 짠 근거 (서버 공고 예산은 다나와가 아니라 여기서 나온다)
    2) 우리가 이겨야 하는 **천장** (bound=upper)
    3) 계약업체 명단 = 경쟁사 지도

  그래서 관측은 전부 acquisition="contract", bound="upper"로 들어간다.

주의
  - 구 도메인 shopping.g2b.go.kr 은 죽었다. shop.g2b.go.kr 을 쓴다.
  - 키워드 검색은 OR라서 "H100"을 넣으면 울타리·소독기까지 44,844건이 나온다.
    **반드시 세부품명번호(dtlsPrnmNo)로 좁힐 것.**

사용:
  python g2b_shop.py --discover "GPU서버"        # 관련 세부품명번호 찾기
  python g2b_shop.py --dtls 4321150102          # 한 품명 전량 수집
  python g2b_shop.py --sync                     # 화이트리스트 전량 수집 → 스냅샷
  python g2b_shop.py --find "Xeon Gold 6442Y"   # 로컬 스냅샷에서 앵커 조회
  python g2b_shop.py --dtls 4321150102 --register   # 가격 DB에 관측으로 등록
"""
import os, re, sys, json, time, html, argparse, datetime, collections
import urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import price_schema as ps

API = "https://shop.g2b.go.kr/gm/gms/gmsd/newShopUntySrchApi.do"
LINK = "https://shop.g2b.go.kr/link/GMSF001_01/?ctrtItemMngNo=%s"
SNAP = os.path.join(ps.DATA, "g2b_shop_catalog.json")

# IT 하드웨어 세부품명 화이트리스트. --discover 로 늘린다.
WHITELIST = {
    "4321150102": "컴퓨터서버",       # GPU서버도 여기 들어있다
    "4321159601": "베어본컴퓨터",
    "4321150701": "데스크톱컴퓨터",
    "4321150301": "노트북컴퓨터",
    "4320140101": "그래픽용어댑터",     # GPU 단품
    "4320140201": "기억유닛",           # RAM
    "4320183001": "SSD저장장치",
    "4320180202": "레이드저장장치",
}


def _post(vo, timeout=25):
    req = urllib.request.Request(
        API, data=json.dumps({"searchVO": vo}).encode("utf-8"),
        headers={"Content-Type": "application/json;charset=UTF-8",
                 "Menu-Info": "{}", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _clean(s):
    """조달몰은 괄호를 &#40; 로 내려준다. 두 번 풀어야 원문이 된다."""
    return html.unescape(html.unescape(str(s or ""))).strip()


def _attrs(it):
    """pdctAtrbNm(이름) + pdctAtrbCdDtlNm(값)을 짝지어 규격 dict로."""
    names, vals = _clean(it.get("pdctAtrbNm")), _clean(it.get("pdctAtrbCdDtlNm"))
    if not names or not vals:
        return {}
    # 세그먼트 형식: 01$1000000$001$000000$프로세서(CPU)종류$ATTR270651
    # 마지막이 ATTR 코드, 그 앞이 사람이 읽는 속성명이다
    ns = []
    for seg in names.split("|"):
        parts = seg.split("$")
        ns.append(parts[-2] if len(parts) >= 2 else parts[-1])
    vs = vals.split("$")
    return {n: v for n, v in zip(ns, vs) if n and v and not n.isdigit()}


def fetch_page(vo, page, size=100):
    v = dict(vo)
    v.update({"recordCountPerPage": size, "currentPage": page})
    j = _post(v)
    return j.get("totalSize") or 0, (j.get("rsltList") or [])


def collect(vo, max_rows=2000, label=""):
    rows, page = [], 1
    total, first = fetch_page(vo, 1)
    rows += first
    while len(rows) < min(total, max_rows) and first:
        page += 1
        _, more = fetch_page(vo, page)
        if not more:
            break
        rows += more
        time.sleep(0.3)
    out = []
    for it in rows[:max_rows]:
        mng = it.get("ctrtItemMngNo")
        out.append({
            "key": "g2bshop:%s" % (it.get("itemIdnfNo") or mng),
            "ctrtItemMngNo": mng,
            "itemIdnfNo": it.get("itemIdnfNo"),
            "name": _clean(it.get("itemIdnfNm")),
            "dtlsPrnm": _clean(it.get("dtlsPrnm")),
            "dtlsPrnmNo": it.get("dtlsPrnmNo"),
            "price": int(it.get("ctrtUprc") or 0),
            "vendor": _clean(it.get("mnftrEtpsNm") or it.get("ctentUntyGrpNm")),
            "ctrtNo": it.get("ctrtNo"),
            "ctrt_end": str(it.get("ctrtEndYmd") or "")[:8],
            "delivery_days": it.get("dlvgdsTermNody"),
            "delivery_cond": _clean(it.get("devyCndtNm")),
            "direct_mfg": it.get("drctPrdctnEtpsYn"),
            "ent_form": _clean(it.get("entFormSeNm")),
            "attrs": _attrs(it),
            "url": LINK % mng if mng else None,
        })
    if label:
        print("  %-16s %5d건 수집 (전체 %d)" % (label, len(out), total))
    return out


def discover(keyword, top=25):
    """키워드로 훑어 관련 세부품명번호를 뽑는다. OR 검색이라 결과가 지저분하니 빈도로 거른다."""
    _, rows = fetch_page({"searchKeyword": keyword}, 1, size=100)
    c = collections.Counter((r.get("dtlsPrnmNo"), _clean(r.get("dtlsPrnm"))) for r in rows)
    print("[%s] 세부품명 후보" % keyword)
    for (no, nm), n in c.most_common(top):
        mark = " *등록됨" if no in WHITELIST else ""
        print("   %-12s %-24s %3d건%s" % (no, nm, n, mark))
    return c


def save_snapshot(items):
    payload = {"source": "shop.g2b.go.kr (newShopUntySrchApi)",
               "collected": datetime.date.today().isoformat(),
               "count": len(items), "items": items}
    json.dump(payload, open(SNAP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("스냅샷 저장: %s (%d건)" % (SNAP, len(items)))


def load_snapshot():
    if not os.path.exists(SNAP):
        return []
    return json.load(open(SNAP, encoding="utf-8")).get("items", [])


def find(terms, limit=25):
    items = load_snapshot()
    if not items:
        print("스냅샷이 없다. 먼저 --sync 를 돌려라.")
        return []
    toks = [t.lower() for t in terms]
    hits = [i for i in items
            if all(t in (i["name"] + " " + json.dumps(i["attrs"], ensure_ascii=False)).lower()
                   for t in toks)]
    hits.sort(key=lambda i: i["price"])
    print("'%s' → %d건 (계약단가 = 상한, 원가 아님)" % (" ".join(terms), len(hits)))
    for i in hits[:limit]:
        print("  %12s원  %-18s %s" % (format(i["price"], ","), i["vendor"][:18], i["name"][:78]))
        if i["attrs"]:
            print("               %s" % json.dumps(i["attrs"], ensure_ascii=False)[:150])
        print("               %s" % i["url"])
    return hits


def register(items):
    """가격 DB에 계약단가 관측으로 등록. bound=upper 로 못박는다."""
    products = ps.load_products()
    recs, n_new = [], 0
    for it in items:
        if not it["price"] or not it["url"]:
            continue
        k = it["key"]
        if k not in products:
            n_new += 1
            products[k] = {
                "name": it["dtlsPrnm"] or it["name"][:40],
                "brand": it["vendor"], "model_code": None,
                "spec": it["name"], "urls": [it["url"]], "aliases": [],
                "qty_basis": "개당", "volatility": "normal",
                "first_seen": datetime.date.today().isoformat(),
                "note": "조달 종합쇼핑몰 계약단가(상한). 계약 %s~%s, 납품 %s일, %s" % (
                    it.get("ctrtNo"), it.get("ctrt_end"),
                    it.get("delivery_days"), it.get("delivery_cond")),
                "g2b_attrs": it["attrs"],
            }
        recs.append(ps.observe(
            k, it["price"], vendor="%s(조달 계약단가)" % it["vendor"],
            method="contract", url=it["url"], observer="script:g2b_shop",
            acquisition="contract", bound="upper", vendor_scope="named",
            vat_included=True))
    ps.save_products(products)
    ps.append_obs(recs)
    ps.rebuild_view()
    print("등록: 관측 %d건 (신규 상품 %d개). 전부 bound=upper" % (len(recs), n_new))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dtls")
    ap.add_argument("--keyword")
    ap.add_argument("--discover")
    ap.add_argument("--find", nargs="+")
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--max", type=int, default=2000)
    a = ap.parse_args()

    if a.discover:
        discover(a.discover)
        return
    if a.find:
        find(a.find)
        return

    items = []
    if a.sync:
        print("화이트리스트 %d개 품명 수집" % len(WHITELIST))
        for no, nm in WHITELIST.items():
            try:
                items += collect({"dtlsPrnmNo": no}, a.max, label=nm)
            except Exception as e:
                print("  %-16s 실패: %s" % (nm, e))
        # 기존 스냅샷과 병합 (키 기준 최신 우선)
        merged = {i["key"]: i for i in load_snapshot()}
        merged.update({i["key"]: i for i in items})
        items = list(merged.values())
        save_snapshot(items)
    elif a.dtls:
        items = collect({"dtlsPrnmNo": a.dtls}, a.max, label=a.dtls)
    elif a.keyword:
        items = collect({"searchKeyword": a.keyword}, a.max, label=a.keyword)
    else:
        ap.print_help()
        return

    if a.register:
        register(items)


if __name__ == "__main__":
    main()
