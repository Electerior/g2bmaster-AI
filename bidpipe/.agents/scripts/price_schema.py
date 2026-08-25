# -*- coding: utf-8 -*-
"""가격 데이터 스키마 — 상품 정체성(products)과 관측(observations)을 분리한다.

왜 나눴나 (2026-08-20 사용자 확정)
  이 DB는 '에이전트가 웹에서 본 것의 기록'이고, 사용자는 그 화면을 본 적이 없다.
  그래서 저장 형태보다 **누가·언제·어떤 방법으로 봤고 나중에 검증 가능한가**가 중요하다.
  상품 URL은 '어느 상품인가'를 고정할 뿐 '관측 시점에 얼마였나'를 증명하지 못한다.

구조
  products.json        상품 정체성 (안 변하는 것). key → {name, brand, model_code, spec, urls[]}
  observations.jsonl   관측 (변하는 것). append-only. 절대 덮어쓰지 않는다.
  price-db.json        위 둘에서 재생성하는 **파생 뷰**. 손으로 고치지 말 것.

신뢰도 티어 — 기준은 오직 '나중에 재현 가능한가'
  A  당사 매입가표 / 공식 견적서            확정 원가
  B  특정 판매처 상품 상세 실측             판매처가 특정돼 재현 가능
  C  다나와 최저가 스냅샷                   상품만 특정, 판매처 비특정 → 재현 불가
  D  추정 / 환율환산(rate 없음) / 출처불명   **투찰 근거로 사용 금지**
"""
import os, re, json, datetime
from urllib.parse import urlparse, parse_qs, quote

ROOT = os.environ.get("BIDPIPE_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, ".agents/data/price")
PRODUCTS = os.path.join(DATA, "products.json")
OBS = os.path.join(DATA, "observations.jsonl")
RETRACT = os.path.join(DATA, "retractions.jsonl")
VIEW = os.path.join(ROOT, ".agents/skills/price-research/references/price-db.json")
os.makedirs(DATA, exist_ok=True)

TIERS = ("A", "B", "C", "D")
TIER_DESC = {
    "A": "당사 매입가표/공식 견적서 (확정 원가)",
    "B": "특정 판매처 상품 상세 실측 (재현 가능)",
    "C": "다나와 최저가 스냅샷 (판매처 비특정)",
    "D": "추정/환율환산/출처불명/만료견적 (투찰 근거 금지)",
}

# ── 관측의 성격 (2026-08-20 추가) ─────────────────────────
# tier 는 '재현 가능한가'라는 판정이고, 아래 둘은 '무엇을 봤나'라는 사실이다. 직교한다.
#
# bound 가 특히 중요하다. 조달 계약단가나 개찰가 역산은 '그 가격이다'가 아니라
# '그보다 비쌀 수 없다'는 **상한**이다. 상한을 상한이라 적지 않으면 다음 사람이
# 그걸 단가로 읽고, 그 순간 추정가가 된다 (규칙 1 위반).
BOUNDS = ("point", "upper", "lower")
BOUND_DESC = {
    "point": "실제 판매가/견적가 — 그 값이 곧 단가",
    "upper": "상한만 안다 (조달 계약단가·개찰가 역산) — 원가 행에 그대로 쓰면 안 됨",
    "lower": "하한만 안다 (부분 구성가 합 등)",
}

# 어떻게 얻었나. 갱신 방법과 신선도 정책이 여기서 갈린다.
ACQUISITIONS = ("scrape", "contract", "quote", "derived", "fx", "internal")
ACQUISITION_DESC = {
    "scrape": "판매 페이지 직접 수집 (URL 재조회로 추적 가능)",
    "contract": "조달 종합쇼핑몰 계약단가 등 공공 계약 공시",
    "quote": "업체 견적서/단가표 (URL 없음 → valid_until 로 추적)",
    "derived": "구성 차분 역산 (완제품 SKU 2건의 가격차)",
    "fx": "해외가 환산 (참고 앵커, 투찰 근거 금지)",
    "internal": "당사 매입가표",
}

# 경로 마지막 토큰이 이런 단어면 그건 상품 ID가 아니라 라우트 이름이다.
# (2026-08-20 사고: allserver.co.kr/products/info?prdId=... 의 'info'가 키로 잡혀
#  올서버 592개 상품이 전부 'allserver.co.kr:info' 하나로 붕괴했다. Xeon Silver 4510
#  121만원 자리에 2U GPU 베어본 1,250만원이 덮여 KETI 건 마진이 19.1% → -72.3%로 뒤집혔다)
_GENERIC_TAILS = {
    "info", "detail", "details", "view", "product", "products", "goods", "item",
    "items", "index", "list", "shop", "main", "page", "read", "show", "prd",
}


# ── 상품 키 ──────────────────────────────────────────────
def product_key(url):
    """URL에서 상품 고유키를 뽑는다. 이름이 아니라 이게 진짜 식별자다."""
    u = (url or "").strip()
    if not u.lower().startswith("http"):
        return None
    p = urlparse(u)
    q = parse_qs(p.query)
    host = p.netloc.replace("www.", "").replace("m.", "").replace("item.", "")
    if "danawa.com" in p.netloc:
        pc = (q.get("pcode") or q.get("code") or [""])[0]
        return "danawa:" + pc if pc else None
    # 상품 식별 쿼리 파라미터. 표기(대소문자)가 사이트마다 달라 소문자로 맞춰 비교한다.
    ql = {k.lower(): v for k, v in q.items()}
    # 조달 종합쇼핑몰은 계약품목관리번호가 상품 식별자다. 경로는 전부 /link/GMSF001_01/로 같다
    if "g2b.go.kr" in p.netloc:
        for k in ("ctrtitemmngno", "itemidnfno", "prdctidntno"):
            if ql.get(k):
                return "g2bshop:%s" % ql[k][0]
    # 쿼팡은 경로의 productId가 정식 상품키다. itemId는 옵션 단위라
    # 같은 상품이어도 유입 경로에 따라 달라진다 → 키가 갈라진다
    if "coupang.com" in p.netloc:
        m = re.search(r"/vp/products/(\d+)", p.path)
        if m:
            return "coupang.com:%s" % m.group(1)
    for k in ("goodscode", "productno", "gid", "it_id", "product_no", "productid",
              "prdid", "prd_id", "goodsno", "goods_no", "itemid", "item_id",
              "prdtno", "branduid", "ctrtitemmngno", "nprodcode", "prodcode", "itemno"):
        if ql.get(k):
            return "%s:%s" % (host, ql[k][0])
    # 경로 안의 상품 슬러그. **전체 세그먼트**를 잡아야 한다.
    # (예전엔 (\w+)이라 /product/nvidia-rtx-pro-6000-... 가 'nvidia'에서 끈겨
    #  같은 몰의 NVIDIA 상품이 전부 한 키로 뭉칠 수 있었다)
    m = re.search(r"/(?:products?|vp/products|p/product)/(.+)$", p.path)
    if m and m.group(1).strip("/").lower() not in _GENERIC_TAILS:
        from urllib.parse import unquote
        # 마지막 세그먼트만 생족하면 /product/ai-hardware/nvidia-rtx-5090 같은 URL에서
        # 카테고리('ai-hardware')를 상품으로 잃는다 → 뒤를 통째로 쓴다
        return "%s:%s" % (host, _slug(unquote(m.group(1)))[:56])
    # 경로가 라우트만 남았을 때 마지막으로 보는 범용 번호 파라미터.
    # 먼저 보면 엉뚱한 것을 잡을 수 있어 순서가 중요하다 (/goods/view?no=3694)
    for k in ("no", "idx", "seq", "code"):
        v = (ql.get(k) or [""])[0]
        if v and re.fullmatch(r"[A-Za-z0-9_\-]{2,}", v):
            return "%s:%s" % (host, v)
    # Dell 한국몰: /apd/338-crqh/... 가 상품코드. 경로에 한글이 섞여 키가 지저분해지는 걸 막는다
    m = re.search(r"/apd/([A-Za-z0-9\-]+)", p.path)
    if m:
        return "%s:%s" % (host, m.group(1).lower())
    # 마지막 수단: 경로를 짧은 슬러그로. percent-encoding을 풀어야 같은 URL이 같은 키가 된다
    from urllib.parse import unquote
    tail = _slug(unquote(p.path))[:48] or "root"
    # 라우트 이름만 남았다면 상품이 특정되지 않은 것이다. 키를 만들지 말고 포기한다
    # (여기서 억지로 키를 만들면 같은 사이트 전 상품이 한 키로 뭉친다)
    if tail in _GENERIC_TAILS or tail == "root":
        return None
    return "%s:%s" % (host, tail)


# 제조사 모델코드로 보이는 토큰. MZ-V9P2T0BW, ESC4000A-E12, I350-T4, 9560-16i ...
_MODEL_PATS = [
    re.compile(r"\b([A-Z]{2}-[A-Z0-9]+(?:/[A-Z0-9]+)*)\b"),      # MZ-77E250BW, MU-PC1T0R/H/T
    re.compile(r"\b([A-Z]{2,}[0-9]{3,}[A-Z0-9]*(?:-[A-Z0-9]+)+)\b"),  # ESC4000A-E12, E810-XXVDA2
    re.compile(r"\b([A-Z][0-9]{3}-[A-Z0-9]+)\b"),                 # I350-T4
    re.compile(r"\b([0-9]{4}-[0-9]+[a-z]+)\b"),                   # 9560-16i
]

_CHANNEL_SLUG = [
    ("당사 매입", "internal"), ("매입 채널", "internal"),
    ("아이티마야", "quote"), ("itmaya", "quote"), ("올서버", "quote"),
    ("엑스디노드", "quote"), ("리더스시스템즈", "quote"), ("견적", "quote"),
]


# 자주 쓰는 채널명은 영문 슬러그로 고정한다 (키가 파일명·URL에 쓰일 수 있어서)
_CHANNEL_ALIAS = {
    "삼성 ssd 유통": "samsung-ssd", "삼성-ssd-유통": "samsung-ssd",
    "아이티마야": "itmaya", "올서버": "allserver", "엑스디노드": "xdnode",
    "리더스시스템즈": "leaderssys", "슈가큐브네트웍스": "sugarcube",
}


def _slug(s):
    s = re.sub(r"\(.*?\)", "", s or "").strip()
    s = re.sub(r"[\s/]+", "-", s)
    s = re.sub(r"[^0-9A-Za-z가-힣\-_.]", "", s)
    return s.strip("-").lower()[:40] or "unknown"


def extract_model_code(*texts):
    """spec/name에서 제조사 모델코드를 뽑는다. 없으면 None."""
    for t in texts:
        if not t:
            continue
        for pat in _MODEL_PATS:
            m = pat.search(str(t))
            if m:
                return m.group(1)
    return None


def synthetic_key(vendor, name, spec):
    """URL이 없는 관측(매입가표·견적)의 키. 사용자 확정 규칙: 채널:모델코드"""
    v = vendor or ""
    prefix = "unlinked"
    for needle, slug in _CHANNEL_SLUG:
        if needle in v:
            prefix = slug
            break
    # 채널 이름: '당사 매입 채널(삼성 SSD 유통)' → samsung-ssd
    # 별칭은 부분일치로 본다. vendor에 전화번호나 수식어가 붙어 있어도 채널을 잡아내야
    # 'quote:아이티마야-02-713-1256/...' 같은 키가 안 생긴다.
    inner = re.search(r"\((.*?)\)", v)
    raw = inner.group(1) if inner else v
    chan = None
    for needle, slug in _CHANNEL_ALIAS.items():
        if needle.replace("-", " ") in raw.lower().replace("-", " "):
            chan = slug
            break
    chan = chan or _slug(raw)
    code = extract_model_code(spec, name) or _slug("%s-%s" % (name, spec))[:30]
    return "%s:%s/%s" % (prefix, chan, code)


def make_key(url, vendor, name, spec):
    return product_key(url) or synthetic_key(vendor, name, spec)


# ── 티어 판정 ─────────────────────────────────────────────
def infer_acquisition(vendor, url, method=None):
    """관측을 어떻게 얻었는가(사실). tier(판정)와 별개 축이다."""
    v, u, m = (vendor or ""), (url or ""), (method or "")
    if m in ACQUISITIONS:
        return m
    if m in ("매입가표", "internal") or "당사 매입" in v or "매입 채널" in v:
        return "internal"
    if "shop.g2b.go.kr" in u or "계약단가" in v:
        return "contract"
    if "견적" in v or "단가표" in v:
        return "quote"
    if "환산" in v:
        return "fx"
    return "scrape"


def _expired(valid_until, as_of=None):
    """견적·단가표가 만료됐는가. 날짜 문자열(YYYY-MM-DD) 비교."""
    if not valid_until:
        return False
    today = (as_of or datetime.date.today().isoformat())[:10]
    return str(valid_until)[:10] < today


def infer_tier(vendor, url, method=None, valid_until=None, quote_source=None,
               as_of=None, evidence_urls=None):
    """관측 하나의 재현 가능성을 등급으로.

    핵심 판정선은 '같은 화면을 나중에 다시 열어 같은 값을 볼 수 있는가'다.
    다나와는 **가격비교 페이지**라 최저가 판매처와 금액이 수시로 바뀐다. 스크레이핑 순간
    판매처 이름이 찍혔다고 해서 재현 가능한 게 아니다 (2026-08-20 실증: 최저가 배지가
    이미 품절/판매중지된 매물을 가리키는 사례). 그래서 다나와 출처는 판매처 유무와
    무관하게 항상 C다. B는 판매처 자체 상품 페이지를 직접 연 경우에만 준다.
    """
    v = (vendor or "")
    u = (url or "").strip()
    has_url = u.lower().startswith("http")
    acq = infer_acquisition(vendor, url, method)

    # 만료된 견적/단가표는 더 이상 근거가 아니다. 예외 없이 D.
    # (이게 없으면 견적은 넣는 순간 화석이 된다 — 누가 언제 받은 건지 아무도 모른다)
    if _expired(valid_until, as_of):
        return "D"
    if acq == "internal":
        return "A"
    if any(k in v for k in ("추정", "미확인")):
        return "D"
    if acq == "fx" or "환산" in v:
        return "D"                      # 해외가 환산 = 참고 앵커, 투찰 근거 금지
    if acq == "quote":
        # 스킬 문서 기준: 공식 견적서 = A. 단, 견적처가 적힐 있을 때만.
        # "견적 필요"라는 표시만 있는 건 견적이 아니라 견적이 없다는 뜻이다 → D
        return "A" if quote_source else "D"
    if acq == "derived":
        # 구성 차분: 근거 URL 2개가 붙어야 재현 가능하다
        return "B" if (evidence_urls and len(evidence_urls) >= 2) else "D"
    if acq == "contract":
        return "B" if has_url else "D"  # 조달몰은 고정 URL이 있어 재방문 가능
    if not has_url:
        return "D"                      # 무엇을 봤는지 되짚을 수 없다
    if method == "danawa" or "danawa.com" in u or "최저가" in v:
        return "C"                      # 가격비교 스냅샷 = 판매처 비특정
    return "B"                          # 판매처 자체 상품 페이지


def infer_bound(acquisition):
    """그 값이 단가인가, 상한인가."""
    return "upper" if acquisition in ("contract", "outcome") else "point"


# ── 저장소 I/O ────────────────────────────────────────────
def load_products():
    if not os.path.exists(PRODUCTS):
        return {}
    return json.load(open(PRODUCTS, encoding="utf-8"))


def save_products(products):
    json.dump(products, open(PRODUCTS, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, sort_keys=True)


def append_obs(records):
    """관측은 append만. 과거 관측을 고치지 않는다."""
    with open(OBS, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def iter_obs():
    if not os.path.exists(OBS):
        return
    with open(OBS, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def load_retractions():
    """철회된 관측 집합. observations.jsonl은 append-only라 과거 레코드를 고치지 않는다.
    대신 (key, observed_at)를 여기에 쌓아 **무효화**한다. 이도 append-only."""
    out = set()
    if not os.path.exists(RETRACT):
        return out
    with open(RETRACT, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            out.add((r.get("key"), r.get("observed_at")))
    return out


def retract(records):
    """[{key, observed_at, reason}] 를 철회 로그에 추가."""
    with open(RETRACT, "a", encoding="utf-8") as fh:
        for r in records:
            r.setdefault("retracted_at",
                         datetime.datetime.now().isoformat(timespec="seconds"))
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def latest_by_key(require_price=True):
    """상품키별 최신 관측. 같은 시각이면 티어가 높은 쪽을 남긴다."""
    latest = {}
    retracted = load_retractions()
    for r in iter_obs():
        k = r.get("key")
        if not k or (require_price and not r.get("price")):
            continue
        if (k, r.get("observed_at")) in retracted:
            continue
        ts = r.get("observed_at") or r.get("scraped_at") or ""
        cur = latest.get(k)
        if not cur:
            latest[k] = r
            continue
        cts = cur.get("observed_at") or cur.get("scraped_at") or ""
        if ts > cts or (ts == cts and
                        TIERS.index(r.get("tier", "D")) < TIERS.index(cur.get("tier", "D"))):
            latest[k] = r
    return latest


def observe(key, price, vendor=None, method="fetch", tier=None, url=None,
            observer="script:price_refresh", **extra):
    """관측 레코드 하나를 만든다(기록은 append_obs로)."""
    valid_until = extra.pop("valid_until", None)
    quote_source = extra.pop("quote_source", None)
    qty_tier = extra.pop("qty_tier", None)
    evidence_urls = extra.pop("evidence_urls", None)
    acquisition = extra.pop("acquisition", None) or infer_acquisition(vendor, url, method)
    bound = extra.pop("bound", None) or infer_bound(acquisition)
    rec = {
        "key": key,
        "observed_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "observer": observer,
        "method": method,
        "acquisition": acquisition,
        "bound": bound,
        "valid_until": valid_until,
        "quote_source": quote_source,
        "qty_tier": qty_tier,
        "evidence_urls": evidence_urls,
        "tier": tier or infer_tier(vendor, url, method, valid_until=valid_until,
                                   quote_source=quote_source, evidence_urls=evidence_urls),
        "price": price,
        "currency": extra.pop("currency", "KRW"),
        "fx_rate": extra.pop("fx_rate", None),
        "vat_included": extra.pop("vat_included", True),
        "vendor": vendor,
        "vendor_scope": extra.pop("vendor_scope", None) or (
            "internal" if "당사 매입" in (vendor or "") else
            "lowest" if "최저가" in (vendor or "") else
            "named" if vendor else None),
        "listing_status": extra.pop("listing_status", "active"),
        "genuine": extra.pop("genuine", None),
        "url": url,
    }
    rec.update({k: v for k, v in extra.items() if v is not None})
    return rec


# ── 파생 뷰 재생성 ────────────────────────────────────────
def rebuild_view():
    """products + 최신 관측 → price-db.json. AI가 읽는 조회용 스냅샷이다.

    사용자 확정(2026-08-20): 현재가 스냅샷 파일은 유지하되 **스크립트만 쓴다.**
    손으로 편집하면 다음 재생성에서 사라진다.
    """
    products, latest = load_products(), latest_by_key()
    rows = []
    for key, p in products.items():
        o = latest.get(key)
        if not o:
            continue
        _u = o.get("url") or (p.get("urls") or [None])[0]
        _acq = o.get("acquisition") or infer_acquisition(o.get("vendor"), _u, o.get("method"))
        tier_now = infer_tier(o.get("vendor"), _u, o.get("method"),
                              valid_until=o.get("valid_until"),
                              quote_source=o.get("quote_source"),
                              evidence_urls=o.get("evidence_urls"))
        rows.append({
            "acquisition": _acq,
            "bound": o.get("bound") or infer_bound(_acq),
            "valid_until": o.get("valid_until"),
            "expired": _expired(o.get("valid_until")),
            "qty_tier": o.get("qty_tier"),
            "evidence_urls": o.get("evidence_urls"),
            "key": key,
            "name": p.get("name"),
            "spec": p.get("spec"),
            "qty_basis": p.get("qty_basis", "개당"),
            "price": o.get("price"),
            "vat_included": o.get("vat_included", True),
            "vendor": o.get("vendor"),
            "vendor_scope": o.get("vendor_scope"),
            "url": o.get("url") or (p.get("urls") or [None])[0],
            "date": (o.get("observed_at") or "")[:10],
            "tier": tier_now,
            "tier_at_observation": o.get("tier"),
            "method": o.get("method"),
            "observer": o.get("observer"),
            "listing_status": o.get("listing_status"),
            "genuine": o.get("genuine"),
            "currency": o.get("currency", "KRW"),
            "fx_rate": o.get("fx_rate"),
            "volatility": p.get("volatility", "normal"),
            "note": p.get("note", ""),
            "source_file": o.get("source_file") or p.get("source_file", ""),
        })
    rows.sort(key=lambda r: (r["tier"] or "Z", r["name"] or ""))
    payload = {
        "_generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "_warning": "이 파일은 products.json + observations.jsonl에서 자동 재생성된다. 직접 수정하지 말 것 (price_schema.rebuild_view)",
        "_tiers": TIER_DESC,
        "_bounds": BOUND_DESC,
        "_acquisitions": ACQUISITION_DESC,
        "items": rows,
    }
    json.dump(payload, open(VIEW, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return rows
