# -*- coding: utf-8 -*-
"""상품키 붕괴 정정 — 잘못된 키로 기록된 관측을 올바른 키로 다시 건다.

배경 (2026-08-20)
  product_key()가 allserver의 `prdId`를 몰라 `/products/info` 경로의 'info'를 키로 잡았다.
  그 결과 올서버 상품 전체가 `allserver.co.kr:info` 한 키로 붕괴했고,
  KETI GPU오케스트레이션 건에서 Xeon Silver 4510(1,216,000)이 2U GPU 베어본(12,500,000)으로
  덮여 마진이 19.1% → -72.3%로 뒤집혔다.

원칙
  observations.jsonl은 append-only다. 과거 레코드를 고치지 않는다.
  대신 (1) retractions.jsonl에 무효화를 append하고
       (2) 올바른 키로 정정 관측을 append한다 (rekeyed_from으로 출처를 남긴다).

사용:  python migrate_keys.py [--dry]
"""
import sys, json, datetime
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import price_schema as ps

DRY = "--dry" in sys.argv


def main():
    obs = list(ps.iter_obs())
    retracted = ps.load_retractions()
    products = ps.load_products()

    fixes, new_obs, retractions = [], [], []
    for r in obs:
        old = r.get("key")
        url = r.get("url")
        if not old or not url:
            continue
        if (old, r.get("observed_at")) in retracted:
            continue
        new = ps.product_key(url)
        if not new or new == old:
            continue
        fixes.append((old, new, r))
        retractions.append({
            "key": old, "observed_at": r.get("observed_at"),
            "reason": "product_key 붕괴 정정 → %s" % new,
        })
        c = dict(r)
        c["key"] = new
        c["rekeyed_from"] = old
        c["rekeyed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        c.setdefault("acquisition", ps.infer_acquisition(r.get("vendor"), url, r.get("method")))
        c["bound"] = c.get("bound") or ps.infer_bound(c["acquisition"])
        new_obs.append(c)

    # products.json 키도 같이 옮긴다 (URL이 진짜 식별자다)
    prod_moves = []
    for k in list(products.keys()):
        urls = products[k].get("urls") or []
        if not urls:
            continue
        nk = ps.product_key(urls[0])
        if nk and nk != k:
            prod_moves.append((k, nk))

    print("== 관측 정정 %d건 ==" % len(fixes))
    seen = set()
    for old, new, r in fixes:
        tag = (old, new)
        if tag in seen:
            continue
        seen.add(tag)
        n = sum(1 for o, w, _ in fixes if (o, w) == tag)
        print("  %-28s → %-28s (%d건)" % (old, new, n))
    print("== 상품키 이동 %d건 ==" % len(prod_moves))
    for old, new in prod_moves:
        print("  %-28s → %-28s  %s" % (old, new, (products[old].get("spec") or "")[:50]))

    if DRY:
        print("\n[dry] 기록하지 않음")
        return

    if retractions:
        ps.retract(retractions)
    if new_obs:
        ps.append_obs(new_obs)
    for old, new in prod_moves:
        if new in products:
            # 이미 있으면 URL만 합치고 옛 키는 버린다
            u = set(products[new].get("urls") or []) | set(products[old].get("urls") or [])
            products[new]["urls"] = sorted(u)
        else:
            products[new] = products[old]
        products[new].setdefault("key_history", []).append(old)
        del products[old]
    ps.save_products(products)
    rows = ps.rebuild_view()
    print("\n기록 완료. 뷰 재생성 %d행" % len(rows))


if __name__ == "__main__":
    main()
