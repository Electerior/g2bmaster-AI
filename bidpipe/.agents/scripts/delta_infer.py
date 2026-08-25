# -*- coding: utf-8 -*-
"""구성 차분 역산 — 완제품 가격표에서 '단품가가 없는 부품'의 가격을 뽑아낸다.

왜 (2026-08-20)
  서버 베어본·엔터프라이즈 부품은 단품 판매가가 공개되지 않는다. 그렇다고 추정하면
  규칙 1 위반이다. 그런데 **완제품 가격표는 공개돼 있다.**

    (1) 같은 플랫폼에서 부품 하나만 다른 SKU 2건의 가격차 = 그 부품의 그 채널 실거래가.
        근거 URL이 2개 붙으므로 재현 가능하다 → acquisition=derived, bound=point
    (2) 완제품가 − (값을 아는 부품가 합) = **베어본 상한**.
        일부 부품을 못 빼도 상한은 여전히 상한이다(덜 뺐으면 잔액이 더 클 뿐).
        → acquisition=derived, bound=upper

  둘 다 '추정'이 아니다. 관측된 두 가격의 산술 결과이고, 출처가 남는다.

사용:
  python delta_infer.py                  # 차분 + 베어본 상한 리포트
  python delta_infer.py --register       # 가격 DB에 관측으로 기록
  python delta_infer.py --platform DL380 # 특정 플랫폼만
"""
import os, re, sys, json, argparse, datetime, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import price_schema as ps

CAT = os.path.join(ps.DATA, "allserver_catalog.json")
SYSTEM_CATS = {"HPE", "DELL", "Lenovo", "GPU", "NAS", "SAN", "MSA"}
PART_CATS = {"Processor", "Memory", "Drive"}

# 구성 라벨 정규화. 올서버는 CPU/프로세서/MEM/Memory 를 섞어 쓴다.
FIELD_ALIAS = {
    "cpu": "cpu", "프로세서": "cpu", "processor": "cpu",
    "mem": "mem", "memory": "mem", "ram": "mem", "메모리": "mem",
    "storage": "sto", "hdd": "sto", "ssd": "sto", "disk": "sto", "저장장치": "sto",
    "pwr": "pwr", "power": "pwr", "psu": "pwr", "전원": "pwr",
    "network": "net", "lan": "net", "네트워크": "net",
    "raid": "raid", "gpu": "gpu",
}
LABEL_RE = re.compile(r"([A-Za-z가-힣][A-Za-z가-힣0-9 ]{0,14})\s*:\s*(.+)$")
# CPU 모델 토큰: 4510, 4514Y, 6442Y, 9115, 8380, 6246R ...
CPU_TOKEN = re.compile(r"\b([0-9]{4}[A-Z]{0,2})\b")


def parse_spec(spec):
    """'CPU: EPYC 9115 16C / Memory: 64GB / 1GbE 4P' → {cpu:..., mem:..., rest:...}

    두 가지가 중요하다.
    1) 구분자는 ' / '(공백 포함)다. 그냥 '/'로 자르면 'Xeon 4310 (12C/24T)'가
       '(12C'에서 끈겨 서로 다른 CPU가 같아 보이거나 그 반대가 된다.
    2) 라벨이 없는 조각(10Gb 2P, 800W×2 등)을 **버리지 않고 rest에 모은다.**
       버리면 '네트워크도 다른 두 SKU'가 'CPU만 다른 쌍'으로 보여
       가격차 전액이 CPU 탓으로 잘못 돌아간다.
    """
    out, rest = {}, []
    for chunk in re.split(r"\s+/\s+", re.sub(r"\s+", " ", spec or "").strip()):
        chunk = chunk.strip(" ,")
        if not chunk:
            continue
        m = LABEL_RE.match(chunk)
        key = FIELD_ALIAS.get(m.group(1).strip().lower()) if m else None
        if key and key not in out:
            out[key] = m.group(2).strip(" ,")
        else:
            rest.append(chunk)
    if rest:
        out["rest"] = " | ".join(sorted(rest))
    return out


def platform_of(name):
    """모델명에서 파트넘버 괄호를 떼고 플랫폼 식별자를 만든다."""
    n = re.sub(r"^\(.*?\)\s*", "", name or "").strip()
    t = n.split()
    return " ".join(t[:2]) if len(t) >= 2 else n


def load_catalog():
    if not os.path.exists(CAT):
        print("올서버 카탈로그가 없다: %s" % CAT)
        return []
    return json.load(open(CAT, encoding="utf-8")).get("items", [])


# ── 1. 부품 단품가 색인 (차분이 아니라 그냥 실판매가) ─────────────
def part_index(items):
    """Processor/Memory/Drive 카테고리 = 파트넘버 + 가격이 붙은 단품. 모델 토큰으로 색인."""
    idx = collections.defaultdict(list)
    for it in items:
        if it.get("cat") not in PART_CATS or not it.get("price"):
            continue
        name = it.get("name") or ""
        for tok in set(CPU_TOKEN.findall(name)):
            idx[tok].append(it)
    return idx


# ── 2. 단일 부품 차이 쌍 → 부품 실거래가 ──────────────────────
def delta_pairs(items, platform_filter=None):
    groups = collections.defaultdict(list)
    for it in items:
        if it.get("cat") not in SYSTEM_CATS or not it.get("price"):
            continue
        f = parse_spec(it.get("spec"))
        if len(f) < 2:
            continue
        plat = platform_of(it.get("name"))
        if platform_filter and platform_filter.lower() not in plat.lower():
            continue
        groups[plat].append((it, f))

    def _maincat(u):
        m = re.search(r"mainCategory=(\d+)", u or "")
        return m.group(1) if m else None

    pairs = []
    for plat, rows in groups.items():
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, fa = rows[i]
                b, fb = rows[j]
                # 올서버 mainCategory가 다르면 사실상 다른 제품군이다.
                # 이름 앞 2토큰만으로 뭡으면 'HPE MSA' 같은 단위로 넣어져 가짜 차분이 난다
                if _maincat(a["url"]) != _maincat(b["url"]):
                    continue
                keys = set(fa) | set(fb)
                diff = [k for k in keys if (fa.get(k) or "") != (fb.get(k) or "")]
                if len(diff) != 1 or a["price"] == b["price"]:
                    continue
                k = diff[0]
                if k == "rest":          # 라벨 없는 부분이 다른 건은 무엇이 바낀 건지 모른다
                    continue
                hi, lo = (a, b) if a["price"] > b["price"] else (b, a)
                fhi, flo = (fa, fb) if a["price"] > b["price"] else (fb, fa)
                pairs.append({
                    "platform": plat, "field": k,
                    "delta": hi["price"] - lo["price"],
                    "from": flo.get(k), "to": fhi.get(k),
                    "hi_name": hi["name"], "lo_name": lo["name"],
                    "hi_price": hi["price"], "lo_price": lo["price"],
                    "evidence_urls": [hi["url"], lo["url"]],
                })
    pairs.sort(key=lambda p: (p["field"], -p["delta"]))
    return pairs, groups


# ── 3. 베어본 상한 ──────────────────────────────────────────
def barebone_bounds(groups, pidx):
    """플랫폼별 최저가 완제품에서 값을 아는 부품을 빼고 남은 금액 = 베어본 상한.

    못 뺀 부품이 있어도 상한은 유효하다 (덜 뺐으면 잔액이 더 크다).
    그래서 'subtracted'와 'unknown'을 같이 찍어 얼마나 조인 상한인지 보이게 한다.
    """
    out = []
    for plat, rows in groups.items():
        rows = sorted(rows, key=lambda r: r[0]["price"])
        it, f = rows[0]
        sub, unknown, ev = [], [], [it["url"]]
        cpu = f.get("cpu") or ""
        toks = set(CPU_TOKEN.findall(cpu))
        hit = None
        for t in toks:
            for cand in pidx.get(t, []):
                hit = cand
                break
            if hit:
                break
        if hit:
            # ×2 표기면 2개로 센다
            n = 2 if re.search(r"[×x]\s*2|\b2\b\s*(?:ea|개)", cpu, re.I) else 1
            sub.append({"field": "cpu", "desc": hit["name"], "unit": hit["price"],
                        "qty": n, "amount": hit["price"] * n, "url": hit["url"]})
            ev.append(hit["url"])
        else:
            unknown.append("cpu:%s" % cpu[:40])
        for k in ("mem", "sto", "pwr", "net", "raid", "gpu"):
            if f.get(k):
                unknown.append("%s:%s" % (k, f[k][:32]))
        total_sub = sum(s["amount"] for s in sub)
        out.append({
            "platform": plat, "sku": it["name"], "sku_price": it["price"],
            "subtracted": sub, "subtracted_total": total_sub,
            "unknown": unknown, "barebone_upper": it["price"] - total_sub,
            "evidence_urls": ev,
        })
    out.sort(key=lambda o: o["barebone_upper"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--platform")
    ap.add_argument("--min-delta", type=int, default=0)
    a = ap.parse_args()

    items = load_catalog()
    if not items:
        return
    pidx = part_index(items)
    pairs, groups = delta_pairs(items, a.platform)
    pairs = [p for p in pairs if p["delta"] >= a.min_delta]

    print("카탈로그 %d건 | 완제품 플랫폼 %d개 | 단품 색인 토큰 %d개"
          % (len(items), len(groups), len(pidx)))
    print("\n== 단일 부품 차이 쌍 %d건 (부품 실거래가, bound=point) ==" % len(pairs))
    for p in pairs[:40]:
        print("  [%-14s] %-4s  %s → %s" % (p["platform"][:14], p["field"], p["from"], p["to"]))
        print("        Δ %11s원   (%s원 ↔ %s원)"
              % (format(p["delta"], ","), format(p["lo_price"], ","), format(p["hi_price"], ",")))
        for u in p["evidence_urls"]:
            print("        %s" % u)

    bb = barebone_bounds(groups, pidx)
    print("\n== 베어본/잔여 상한 %d개 플랫폼 (bound=upper) ==" % len(bb))
    for b in bb[:25]:
        print("  [%-16s] %-46s %12s원" % (b["platform"][:16], b["sku"][:46],
                                          format(b["sku_price"], ",")))
        for s in b["subtracted"]:
            print("        − %-42s %10s원 ×%d" % (s["desc"][:42], format(s["unit"], ","), s["qty"]))
        print("        = 상한 %12s원   (미차감: %s)"
              % (format(b["barebone_upper"], ","), ", ".join(b["unknown"])[:80] or "없음"))

    if not a.register:
        print("\n[--register 를 붙이면 가격 DB에 관측으로 기록한다]")
        return

    products, recs = ps.load_products(), []
    today = datetime.date.today().isoformat()
    for p in pairs:
        code = ps.extract_model_code(p["to"]) or ps._slug(p["to"])[:30]
        key = "derived:%s/%s" % (ps._slug(p["platform"])[:20], code)
        if key not in products:
            products[key] = {
                "name": "%s 차분(%s)" % (p["field"].upper(), p["platform"]),
                "brand": None, "model_code": code,
                "spec": "%s → %s (%s)" % (p["from"], p["to"], p["platform"]),
                "urls": p["evidence_urls"], "aliases": [], "qty_basis": "개당",
                "volatility": "normal", "first_seen": today,
                "note": "완제품 SKU 2건의 가격차로 역산. %s / %s" % (p["lo_name"], p["hi_name"]),
            }
        recs.append(ps.observe(key, p["delta"], vendor="올서버(구성 차분)",
                               method="derived", url=p["evidence_urls"][0],
                               observer="script:delta_infer", acquisition="derived",
                               bound="point", evidence_urls=p["evidence_urls"]))
    # 차감한 게 하나도 없으면 '완제품 가격을 그대로 적은 것'과 같다. 상한으로서 정보가 없다
    for b in [x for x in bb if x["subtracted"]]:
        key = "derived:%s/barebone" % ps._slug(b["platform"])[:24]
        if key not in products:
            products[key] = {
                "name": "%s 베어본 잔여(상한)" % b["platform"],
                "brand": None, "model_code": None,
                "spec": "%s 최저 SKU에서 값을 아는 부품 차감 후 잔액" % b["platform"],
                "urls": b["evidence_urls"], "aliases": [], "qty_basis": "개당",
                "volatility": "normal", "first_seen": today,
                "note": "상한. 미차감 항목: %s" % ", ".join(b["unknown"]),
            }
        recs.append(ps.observe(key, b["barebone_upper"], vendor="올서버(구성 차분·상한)",
                               method="derived", url=b["evidence_urls"][0],
                               observer="script:delta_infer", acquisition="derived",
                               bound="upper", evidence_urls=b["evidence_urls"]))
    ps.save_products(products)
    ps.append_obs(recs)
    ps.rebuild_view()
    print("\n기록: 관측 %d건 (차분 %d, 상한 %d)"
          % (len(recs), len(pairs), len([x for x in bb if x["subtracted"]])))


if __name__ == "__main__":
    main()
