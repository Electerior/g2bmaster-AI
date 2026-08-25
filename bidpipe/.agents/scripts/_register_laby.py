# -*- coding: utf-8 -*-
"""랩241(LABY) 강의녹화 장비 가격 앵커 1회성 등록 스크립트.
2026-08-21 조사 결과를 price DB에 반영 (재검색 방지 목적).
"""
import sys, os, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import price_schema as ps

products = ps.load_products()

def add_product(key, name, brand, model_code, spec, url, note):
    entry = products.get(key, {})
    entry.update({
        "name": name,
        "brand": brand,
        "model_code": model_code,
        "spec": spec,
        "urls": sorted(set(entry.get("urls", []) + [url])),
        "aliases": entry.get("aliases", []),
        "qty_basis": "세트당",
        "volatility": "normal",
        "first_seen": entry.get("first_seen", "2026-08-21"),
        "note": note,
        "source_file": "session:2026-08-21_lab241_price_anchor_research",
    })
    products[key] = entry
    return key

items = []

url_lt2 = "https://shop.g2b.go.kr/link/GMSF001_01/?ctrtItemMngNo=R26TA01972719000000001"
k1 = ps.make_key(url_lt2, "주식회사 랩241", "Laby-LT-II", "수업자동녹화시스템, 랩241, Laby-LT-II")
add_product(k1, "Laby-LT-II (수업자동녹화시스템)", "랩241", "Laby-LT-II",
            "LABY-II 2채널 하드웨어 인코딩 녹화장비 + 자동추적카메라(강사추적, LTC2계열 추정) + SW 세트. "
            "물품식별번호 24404510. 조달세트(MAS).",
            url_lt2,
            "다수공급자계약 계약단가=상한(bound=upper). 계약기간 2026-07-24~2027-10-17. "
            "카메라는 자동추적(AI트래킹) 카메라이며 사용자가 문의한 LABY-G320(수동 PTZ, 트래킹 없음)과는 다른 상위 사양 "
            "→ G320으로 대체 시 이 가격보다 낮을 가능성이 높음(미확인).")
items.append((k1, 13200000, url_lt2))

url_a12 = "https://shop.g2b.go.kr/link/GMSF001_01/?ctrtItemMngNo=R26TA01972719000000002"
k2 = ps.make_key(url_a12, "주식회사 랩241", "Laby-A12-II", "수업자동녹화시스템, 랩241, Laby-A12-II")
add_product(k2, "Laby-A12-II (수업자동녹화시스템)", "랩241", "Laby-A12-II",
            "LABY-II 계열 녹화장비 + 자동추적카메라(12배줌 추정) + SW 세트. 물품식별번호 25438996. 조달세트(MAS).",
            url_a12,
            "다수공급자계약 계약단가=상한(bound=upper). 계약기간 2026-07-24~2027-10-17.")
items.append((k2, 15600000, url_a12))

url_a40 = "https://shop.g2b.go.kr/link/GMSF001_01/?ctrtItemMngNo=R26TA01972719000000003"
k3 = ps.make_key(url_a40, "주식회사 랩241", "Laby-A40-II", "수업자동녹화시스템, 랩241, Laby-A40-II")
add_product(k3, "Laby-A40-II (수업자동녹화시스템)", "랩241", "Laby-A40-II",
            "LABY-II 계열 녹화장비 + 자동추적카메라(40배줌 추정) + SW 세트. 물품식별번호 25438997. 조달세트(MAS).",
            url_a40,
            "다수공급자계약 계약단가=상한(bound=upper). 계약기간 2026-07-24~2027-10-17. 3개 SKU 중 최고가.")
items.append((k3, 17250000, url_a40))

url_rec = "https://www.lab241.com/Product/LABYREC"
k4 = "lab241.com:LABY-REC-v3.0"
add_product(k4, "LABY-REC v3.0 (강의 녹화 SW)", "랩241", "Laby-Rec v3.0",
            "통신소프트웨어, 랩241, Laby-Rec v3.0, 강의저장 및 전송소프트웨어, 1Server. "
            "디지털서비스몰 물품식별번호 2436573.",
            url_rec,
            "랩241 공식 홈페이지에 게재된 디지털서비스몰 조달가. 순수 SW 라이선스(1Server)이며 LABY-II 하드웨어와 별개 품목. "
            "디지털서비스몰(digitalmall.g2b.go.kr:8058) 직접 접속은 이 세션 환경에서 타임아웃으로 재확인 불가, "
            "랩241 공식페이지 표기값을 그대로 인용.")
items.append((k4, 3850000, url_rec))

ps.save_products(products)

now = datetime.datetime.now().isoformat(timespec="seconds")
records = []
for key, price, url in items:
    acquisition = "contract"
    vendor = "조달청 종합쇼핑몰 계약단가(랩241)" if "shop.g2b.go.kr" in url else "랩241 공식페이지 게재 디지털서비스몰 조달가"
    rec = ps.observe(
        key, price, vendor=vendor, method="browser", tier="A" if "shop.g2b.go.kr" in url else "B",
        url=url, observer="ai:session-2026-08-21",
        acquisition=acquisition, bound="upper",
        vat_included=True,
        note="랩241 LABY 강의녹화 장비 가격 앵커 조사(2026-08-21). 원가 산정용 상한 참고치.",
    )
    records.append(rec)

ps.append_obs(records)
ps.rebuild_view()

print("등록 완료:", len(records), "건")
for r in records:
    print(" -", r["key"], r["price"], r["vendor"], r["bound"])
