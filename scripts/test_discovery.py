#!/usr/bin/env python3
"""사양→모델 탐색 체인(discover/wiki/hardware_kb)의 순수 함수 검증.

네트워크를 타는 부분은 여기서 부르지 않는다 — 위키 표 파서·숫자 매칭·코퍼스 스코어링만
돈다. 탐색 체인의 계약은 discover.discover_model 의 반환 모양이다
(estimate._resolve_spec_only 가 소비한다).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import wiki                       # noqa: E402
from app.discover import enabled          # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


# ── 숫자 추출 (위키 매칭의 열쇠) ──────────────────────────────────────────────
print("숫자 토큰 추출")

check("콤마 정규화", "10752" in wiki.spec_numbers("CUDA 코어 10,752개"))
check("글자에 붙은 숫자", "16" in wiki.spec_numbers("GDDR7 16GB 메모리") and
      "400" in wiki.spec_numbers("TDP 400W"))
check("소수는 버린다", "16" not in wiki.spec_numbers("16.9 billion"))
check("한 자리는 버린다", "8" not in wiki.spec_numbers("8GB"))
check("중복 제거", wiki.spec_numbers("64GB 64GB") == ["64"])

# ── 위키 표 파서 ──────────────────────────────────────────────────────────────
print("위키 표 파서")

GPU_TABLE = """{| class="wikitable"
! colspan="2" | GeForce RTX
! 5050
! 5060
! 5060 Ti
! 5070
! 5070 Ti
! 5080
! 5090
|-
! colspan="2" | CUDA cores
| 2,560
| 3,840
| 4,608
| 6,144
| 8,960
| 10,752
| 21,760
|-
! colspan="2" | Memory
| 8 GB GDDR7
| 8 GB GDDR7
| 16 GB GDDR7
| 12 GB GDDR7
| 16 GB GDDR7
| 16 GB GDDR7
| 32 GB GDDR7
|-
! colspan="2" | TDP
| 150 W
| 150 W
| 180 W
| 250 W
| 300 W
| 360 W
| 575 W
|}"""

models = wiki.parse_model_tables(GPU_TABLE)
check("모델 열 7개", sorted(models.keys()) ==
      ["5050", "5060", "5060 Ti", "5070", "5070 Ti", "5080", "5090"], str(sorted(models.keys())))
picked = wiki.pick_model("CUDA 코어 10,752개 / GDDR7 16GB / TDP 약 400W", models)
check("5080 을 고른다", picked["model"] == "5080", str(picked))

# 사양 값이 머리에 오는 표(대역폭 표)는 모델 표로 오인하지 않는다
JUNK_TABLE = """{| class="wikitable"
! colspan="2" | Bandwidth
! 1008
! 1344
! 1536
|-
! colspan="2" | GB/s
| 672
| 1008
| 1344
|}"""
check("대역폭 표는 버린다", wiki.parse_model_tables(JUNK_TABLE) == {},
      str(wiki.parse_model_tables(JUNK_TABLE)))

# 사양 데이터 행이 먼저 오는 표는 머리로 인정하지 않는다
SP_TABLE = """{| class="wikitable"
! colspan="2" | Shaders
! 1024
! 1792
! 2048
|-
! colspan="2" | Models
! 9070
! 9070 XT
! 9060 XT
|}"""
check("SP 표의 사양 행은 머리가 아니다", "1024" not in wiki.parse_model_tables(SP_TABLE),
      str(wiki.parse_model_tables(SP_TABLE)))

# CPU 문서 — 맨 숫자 열(메모리 속도)은 브랜드 없이 모델로 인정하지 않는다
CPU_TABLE = """{| class="wikitable"
! colspan="2" | Core Ultra
! 5600
! 4800
! 6400
|-
! colspan="2" | MT/s
| 5600
| 4800
| 6400
|}"""
check("CPU 메모리 속도 열은 버린다", wiki.parse_model_tables(CPU_TABLE, allow_bare=False) == {},
      str(wiki.parse_model_tables(CPU_TABLE, allow_bare=False)))

# ── 하드웨어 KB 스코어링 ─────────────────────────────────────────────────────
print("하드웨어 KB 스코어링")

from app import hardware_kb  # noqa: E402

corpus = [
    {"category": "GPU", "model": "RTX 5080", "text": "CUDA cores 10,752 memory 16 GB GDDR7 TDP 360 W"},
    {"category": "GPU", "model": "RTX 5090", "text": "CUDA cores 21,760 memory 32 GB GDDR7 TDP 575 W"},
]
check("스코어: 5080 이 5090 보다 높다",
      wiki._score_model(["10752", "16"], corpus[0]["text"]) >
      wiki._score_model(["10752", "16"], corpus[1]["text"]),
      "5080 텍스트에 10752·16 이 둘 다 있어야 한다")
check("카테고리 정규화", hardware_kb.canon_category("그래픽카드") == "gpu" and
      hardware_kb.canon_category("Memory") == "ram")

# ── 체인 소스 존재 ────────────────────────────────────────────────────────────
print("탐색 체인 소스")
check("위키는 기본 켜짐", wiki.enabled())
check("체인에 켜진 소스가 있다", enabled())

# ── 다나와 상품 인덱스 (Phase 3) ─────────────────────────────────────────────
print("다나와 상품 인덱스")

from app import product_index  # noqa: E402

LIST_HTML = (
    'id="productItem111"><p class="prod_name">'
    '<strong class="pop_rank"><span class="screen_out">인기 순위</span>1</strong>'
    '<a href="https://prod.danawa.com/info/?pcode=111">COLORFUL iGame 지포스 RTX 5080 ULTRA OC D7 16GB</a></p>'
    'id="productItem222"><p class="prod_name">'
    '<strong class="pop_rank"><span class="screen_out">인기 순위</span>2</strong>'
    '<a href="https://prod.danawa.com/info/?pcode=222">ADATA DDR5-6000 CL30 64GB</a></p>'
)
rows = product_index.parse_list_page(LIST_HTML, "GPU")
check("인기순위 태그를 넘어 이름을 읽는다", len(rows) == 2, str(len(rows)))
check("pcode·url 을 싣는다", rows[0]["pcode"] == "111" and "pcode=111" in rows[0]["url"], str(rows[0]))

# 테스트용 코퍼스 주입 — 실제 파일 mtime 과 맞춰야 load_index 가 이걸 쓴다.
product_index._corpus = (product_index._index_mtime(), [
    {"category": "GPU", "name": "COLORFUL iGame 지포스 RTX 5080 ULTRA OC D7 16GB", "pcode": "111",
     "url": "https://prod.danawa.com/info/?pcode=111"},
    {"category": "GPU", "name": "GIGABYTE AORUS 지포스 RTX 5070 Ti MASTER 16GB", "pcode": "222",
     "url": "https://prod.danawa.com/info/?pcode=222"},
])
hits = product_index.search("RTX 5080", "GPU", k=2)
check("RTX 5080 이 1위", bool(hits) and hits[0][1]["pcode"] == "111", str(hits))
links = product_index.lookup("RTX 5080", "GPU", min_score=2)
check("lookup 이 화이트리스트 링크를 만든다",
      links and links[0]["source"] == "danawa" and links[0]["sourceId"] == "111", str(links))

print()
if FAILURES:
    print(f"{len(FAILURES)}개 실패: {FAILURES}")
    sys.exit(1)
print("scripts/test_discovery.py: OK")
