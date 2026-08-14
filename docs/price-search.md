# 가격 검색 시스템 — studyweb 이후

`POST /api/price/resolve` · `POST /api/price/url` 의 설계 문서.
studyweb 이 빠지고 [CompoPrice](https://github.com/Hong-Iron/CompoPrice) 스크래퍼가 그 자리를 대신하는 것을 전제로 한다.

권위는 `CLAUDE.md §1` 을 따른다. 이 문서는 그 아래, `docs/decisions.md` 와 같은 층이다.
여기서 제안한 것 중 백엔드 합의가 필요한 항목은 `decisions.md §3` 으로 올린다.

---

## 0. 한 줄 요약

studyweb 은 **검색·렌더링·가격파싱 셋을 겸한 범용 서비스**였다.
CompoPrice 는 **좁지만 구조화된 소스**다. 커버리지를 잃고 정확도와 속도를 얻는 교환이며,
가장 큰 소득은 **LLM 가격 추출 단계가 통째로 사라진다**는 것이다.

## 0.1 범위 — 확정 (2026-08-07)

**1차 범위는 품목이 비교적 명확한 것뿐이다** — PC 부품·노트북(다나와), 서버·워크스테이션(ITMAYA).

**인력·용역·일반 소매는 후순위다.** 명확한 품목을 먼저 완성하고 그 뒤에 붙인다.
따라서 §3 의 커버리지 공백은 **결함이 아니라 의도된 범위**다.

이 결정이 설계에 강제하는 것은 하나다 — **범위 밖 품목이 들어와도 죽으면 안 된다.**
`/api/price/resolve` 는 인력·용역 품목명을 정상적으로 받고, 정상적으로
"가격원 없음"을 보고해야 한다. 예외를 던지거나 500 을 내면 안 된다(§5 P-1).

---

## 1. CompoPrice 가 무엇인가

Python 스크래퍼 5개 + 데이터 자산 7개. 가격원은 두 곳이다.

| 스크립트 | 대상 | 산출 |
|---|---|---|
| `scrape_danawa_price.py` (27KB) | 다나와 `prod.danawa.com` | 스펙 필터 검색 → 상품·최저가 |
| `scrape_data_price.py` (19KB) | ITMAYA `itmaya.co.kr` | 서버 상품 + 구성옵션 단가 |
| `itmaya_component_prices.py` (20KB) | (위 산출물 가공) | 서버 CPU/GPU/램/스토리지 단가 색인 |
| `enrich_specs_with_price.py` (11KB) | (결합) | 스펙 CSV 에 최저가·URL 부착 |
| `run_scheduler.py` (7KB) | 배치 러너 | 주기 실행·로그 |

두 사이트 모두 **리버스 엔지니어링으로 내부 AJAX 엔드포인트를 직접 친다.**
다나와는 `getSearchOption.ajax.php` 로 스펙 필터 카탈로그를 받아
`searchAttributeValue[] = {cate}|{attrSeq}|{valueSeq}|OR` 형식으로 조준하고,
ITMAYA 는 `estimate.js` 의 `calcPrice` 계산식(`기본가 + Σ 옵션단가`)을 그대로 재현한다.
HTML 을 LLM 에 던져 읽히는 방식이 아니라 **사이트의 구조를 알고 치는 방식**이다.

---

## 2. 그대로 쓸 수 있는 것

### 2.1 A급 — `/api/price/resolve` 의 뼈대가 된다

| 자산 | 대체 대상 | 비고 |
|---|---|---|
| `search(session, category, spec, pages)` | studyweb `POST /prices` | 반환이 이미 구조화 — **LLM 추출 불필요** |
| `keyword_search()` + `discover_category()` | studyweb `POST /search` | 카테고리를 몰라도 통합검색 → 카테고리 자동 발견 |
| `unmatched_filters` / `notes` / `leftover_terms()` / `text_filter()` | `relaxedQueries` 사다리 | **계약의 `queryRelaxed` 구현체** (§4.3) |
| `request()` 재시도·백오프, `DEFAULT_DELAY_SECONDS=0.8` | — | 절대규칙 #8 "한 겹만" 을 이미 지킨다 |
| `parse_products()` → `{pcode, name, price_krw, url, spec, image_url, collected_at}` | studyweb quote | 백엔드가 대조 가능한 출처 (§4.4) |

조달 품목명은 카테고리를 모르고 들어온다. 따라서 실제 기본 경로는
`category=None` → `keyword_search` → `discover_category` → 스펙 필터 재적용이며,
`search()` 는 이 경로를 이미 갖고 있다.

### 2.2 B급 — 코드보다 값어치가 클 수 있는 데이터 자산

| 파일 | 행 | 가격 채움률 | 쓸모 |
|---|---|---|---|
| `cpu_specs_complete.csv` | 566 | 37% | CPU 스펙 + 다나와 최저가·URL |
| `gpu_specs_sanitized.csv` | 644 | 32% | GPU 스펙 + 최저가·URL |
| `itmaya_options.csv` | 2,533 | 100% | **서버 부품 단가 원본** |
| `itmaya_memory_prices.csv` | 30 | 100% | `price_per_gb` 파생값 포함 |
| `itmaya_storage_prices.csv` | 28 | 100% | `price_per_tb` 파생값 포함 |
| `itmaya_cpu_prices.csv` | 11 | 100% | 서버 CPU 정규화 시트 |
| `danawa_products.csv` | 270 | 100% | 최근 수집 스냅샷 |

ITMAYA 계열이 특히 중요하다. **다나와는 EPYC·Xeon·Threadripper PRO·TESLA/Quadro·
서버 RDIMM·엔터프라이즈 SSD 를 소매로 취급하지 않는다.** 조달 공고의 GPU 서버·워크스테이션
품목은 다나와만으로는 영원히 `null` 이다. `itmaya_component_prices.py` 가 정확히 이 공백을 메운다.

`price_per_gb` / `price_per_tb` 는 studyweb 에 없던 능력이다 — 정확히 같은 모델이 없어도
**용량 스케일 추정**이 가능하다. 다만 추정치를 실측가로 위장하면 안 된다(§4.4).

도메인 지식도 코드에 박혀 있다: `DUAL_CPU_DIVISOR=2`("x 2CPU" 옵션은 2개 묶음가 —
6526Y 4,432,000 → 8,864,000 으로 실증), 0원 옵션은 '별도문의'라 가격 없음 처리.
이런 건 다시 알아내기 비싸다.

### 2.3 C급 — 참고만 하고 이 저장소에 넣지 않는다

- `run_scheduler.py` — 배치 러너. **절대규칙 #10(내구 상태 금지)** 위반이다. 주기 수집은 우리 일이 아니다.
- `enrich_specs_with_price.py` — CSV 를 덮어쓰고 `.bak` 을 남긴다. 파일 쓰기는 우리 것이 아니다.

둘 다 **수집 파이프라인**이고 우리는 **조회 표면**이다. 이 경계를 흐리면 안 된다(§3.1).

---

## 3. 못 하는 것 — studyweb 대비 후퇴

| 능력 | studyweb | CompoPrice | 결과 |
|---|---|---|---|
| 임의 URL 본문 추출 (JS 렌더 포함) | `POST /extract` | **없음** | `/api/price/url` 대체 불가 → §4.5 |
| 인력·용역 단가 (숨고·크몽·알바몬·알바천국·사람인) | 검색 + LLM | **없음** | **후순위 — 의도된 범위 밖** (§0.1) |
| 일반 소매 (쿠팡·11번가·네이버쇼핑·에누리) | 있음 | **없음** | **후순위 — 의도된 범위 밖** (§0.1) |
| 커버리지 범위 | 범용 웹 | 다나와 PC·노트북 + ITMAYA 서버 | 좁아짐 (의도) |
| 응답 시간 | 26~153초 (엔진에 따라) | 페이지당 ~0.8초 + 응답 | **크게 개선** |
| LLM 비용 | 추출마다 1회 | 0회 | **제거** |

첫 줄만 진짜 문제다. 2·3행은 §0.1 에서 후순위로 확정했다.

`app/config.py:26` 의 `DEFAULT_PLATFORMS` 11개 중 CompoPrice 가 커버하는 것은 **0개**다
(다나와는 목록에 없다). 이 상수는 studyweb 의 `includeDomains` 조준용이었으므로 함께 폐기 대상이다.
후순위 품목을 나중에 붙일 때 이 목록을 되살리지 말고 **그때의 가격원에 맞게 새로 정한다** —
숨고·크몽은 검색 도메인 조준으로 풀리는 문제가 아니었다.

### 3.1 경계 — 어디까지가 우리 것인가

```
[별도 프로세스]  CompoPrice 수집기 — 주기 실행, CSV/색인 갱신
        │  읽기 전용 자산
        ▼
[g2bmaster-ai]   /api/price/resolve — 조회·판정·보고만
        │  HTTP
        ▼
[백엔드]         result=null 강제, 캐시, 재시도, 이력
```

`module_a/` · `module_b/` · `korean-law-mcp` 와 같은 패턴이다.
우리가 CSV 를 갱신하기 시작하면 절대규칙 #10 이 깨지고, 프로세스가 죽을 때
잃는 것이 "진행 중이던 요청 하나"를 넘어선다.

---

## 4. 강화한 가격 검색 시스템

### 4.0 파이프라인

```
POST /api/price/resolve  { itemName, hints? }
  │
  0. 식별      itemName → {kind, modelKey, category, qty}      LLM 1회 (선택)
  │
  1. 로컬 색인  cpu/gpu/memory/storage 색인 조회                네트워크 0회, ~1ms
  │            └ 신선도 통과 + 정확 매칭 → 즉시 반환
  │
  2. 다나와     search() / keyword_search()                     ~2-5s
  │
  3. ITMAYA     서버급 키워드일 때만 옵션 단가 색인              네트워크 0회
  │
  4. 보고      result | null + queryRelaxed + misses + sources
```

각 단계는 예외를 던지지 않고 `{ok} | {degrade} | {fatal}` 을 반환한다(절대규칙 #2).

### 4.1 D-1 — LLM 을 가격 추출에서 빼라

studyweb 시절의 `PRICE_INTRO_DEFAULT` + `PRICE_RULES` LLM 추출은
**구조화 소스가 생기면 존재 이유가 없다.** 페이지 본문을 읽혀 숫자를 뽑는 일을
`price_krw` 정수가 대신한다.

LLM 은 **0단계 품목 식별에만** 쓴다:

```
"인텔 제온 6526Y 2CPU 구성 GPU 서버"
  → {kind: "server", cpu: {model: "6526Y", qty: 2}, category: null}
```

이 결정 하나가 시간 부등식을 푼다. 원본의 `PRICES_TIMEOUT` 은 60초였고
(`price-web.js:16`, 주석에 chrome 엔진 153.2초 기록), 이는 절대규칙 #7
**`AI 데드라인 < g2b.ai.timeout-ms < 백엔드 리스`** 를 만족시킬 수 없었다.
LLM 추출을 빼면 예산이 **5초대**로 내려온다.

`pricePrompt` 설정 필드는 이 시점에 의미가 바뀐다 — 추출 프롬프트가 아니라 식별 프롬프트다.
`app/config.py` 에서 이름을 정리하거나, 최소한 주석으로 남긴다.

### 4.2 D-2 — 로컬 색인이 1순위

566 + 644 행의 스펙 DB 에 이미 가격이 붙어 있다. 네트워크 0회로 답이 나오는 질의가 상당수다.
문제는 **신선도**다. `price_checked_at` 은 있는데 조회 시 판정을 안 한다.

정해야 할 것:

- **stale 기준.** 부품 가격은 주 단위로 움직인다. 7일을 시작값으로 제안한다.
- **stale 일 때의 행동.** 데드라인 여유가 있으면 2단계(다나와)를 타고, 없으면
  **stale 값을 쓰되 응답에 `stale: true` 와 `collectedAt` 을 실어 보고**한다.
  조용히 낡은 값을 주는 것이 최악이다.

색인은 **읽기 전용 자산**으로 패키징한다(§3.1). 우리는 읽기만 한다.

### 4.3 D-3 — `queryRelaxed` 를 필터 실패에 결합

계약은 `queryRelaxed` 필수 보고를 요구한다(`CLAUDE.md §3`).
studyweb 시절엔 `relaxedQueries()` 로 질의 문자열을 깎아 내려가며 만들었다 — 무엇을 깎았는지가 불명확했다.

CompoPrice 는 **무엇을 못 지켰는지를 구조적으로 안다:**

| CompoPrice 필드 | 의미 | 계약 매핑 |
|---|---|---|
| `unmatched_filters` | 이 카테고리에 없는 스펙 속성 / 매칭 실패한 값 | `queryRelaxed.droppedFilters` |
| `notes` | 카테고리 자동 발견, 통합검색 대체 등 경로 변경 | `queryRelaxed.notes` |
| `leftover_terms()` → `text_filter()` | 스펙 필터 실패분을 상품명·스펙 문자열로 재검 | `queryRelaxed.textFallback` |
| `applied_filters` | 실제로 적용된 필터 | 지켜진 조건(대조용) |

`resolve_filters()` 의 주석이 이미 정확한 말을 하고 있다 —
*"매칭 실패를 조용히 버리면 필터가 빠진 채 잘못된 결과가 나오므로 반드시 보고한다."*
이건 우리 계약과 같은 원칙이다. 그대로 계약 필드로 승격시키면 된다.

**우리는 보고만 한다.** `result = null` 강제는 백엔드 소유다(`CLAUDE.md §4`).

### 4.4 D-4 — 출처를 대조 가능한 모양으로

`provider: 'studyweb'` 문자열(`price-web.js:193, 241, 545`) 대신:

```json
{
  "source": "danawa",
  "pcode": "103453760",
  "url": "https://prod.danawa.com/info/?pcode=103453760",
  "name": "LG전자 2026 그램 프로16 16Z90U-KU7WK",
  "priceKrw": 3296620,
  "collectedAt": "2026-08-04T10:46:18+09:00",
  "basis": "listed"
}
```

`basis` 를 둔다. 인용 규칙(절대규칙 #5)이 문서에 대해 요구하는 것과 같은 성격이다 —
**백엔드가 원본과 대조할 수 있게 만드는 것이 우리 책임이고, 채택 판정은 백엔드 것이다.**

| `basis` | 뜻 |
|---|---|
| `listed` | 그 URL 에 그 가격이 실제로 표시됐다 |
| `derived` | `price_per_gb` · `DUAL_CPU_DIVISOR` 등으로 환산했다 |
| `stale` | 색인값이고 신선도 기준을 넘겼다 |

`derived` 를 `listed` 로 위장하면 백엔드가 대조에 실패하고,
그 실패의 원인을 우리 쪽에서 찾을 수 없게 된다.

### 4.5 D-5 — `/api/price/url` 은 별도 결정이 필요하다

CompoPrice 에는 `/extract` 대응물이 **없다.** 세 갈래다.

| 안 | 내용 | 평가 |
|---|---|---|
| **(a) 화이트리스트** | 다나와 `pcode` URL · ITMAYA `idx` URL 만 지원, 나머지는 `UNSUPPORTED_SOURCE` 로 거절 | **권장.** SSRF 표면이 화이트리스트로 줄고, 파서를 이미 갖고 있다 |
| (b) 범용 fetch | 우리가 임의 URL 을 받아 파싱 | SSRF 가드를 우리가 지고, JS 렌더 문제가 그대로 재발 |
| (c) 표면 폐기 | 백엔드에 역제안 | 프론트 기능이 사라진다 — 백엔드 확인 필요 |

(a) 를 택하더라도 **`validateExtractUrl` / `isPrivateIp`(`price-web.js:55, 58`) 는 반드시 이식한다.**
studyweb 이 대신 fetch 해 주던 일을 이제 우리 프로세스가 직접 한다면
SSRF 가드는 약해지는 게 아니라 **강해져야 한다.**

---

## 5. CompoPrice 자체를 손봐야 하는 것

이식 전에 고칠 것. 번호는 `decisions.md` 의 `F-` 와 구분해 `P-` 로 둔다.

### P-1 — 범위 밖 품목을 예외로 처리한다 🔴 **§0.1 로 인해 최우선**

`search()` 는 카테고리를 못 찾고 키워드도 없으면 `ValueError` 를 던진다
(`scrape_danawa_price.py:572`). 인력·용역 품목은 **정상적으로** 이 경로를 탄다.

§0.1 에서 그 품목들을 후순위로 확정했으므로, 이 경로는 **예외 상황이 아니라 상시 경로**가 된다.
범위를 좁힌 결정이 이 수정을 선택이 아니라 전제로 만든다.

**절대규칙 #2 위반이다.** 예외를 던지면 "계속 갈 수 있음"과 "여기서 끝"의 구분이 사라진다.
`{status: "no_source", reason: "NO_PRICE_SOURCE"}` 를 반환해야 한다.
가격원이 없는 것은 장애가 아니라 **정직하게 보고할 결과**다.

### P-2 — 가격 채움률 37% / 32%

CPU 566행 중 215행, GPU 644행 중 210행만 가격이 있다.
`cpu_key` 정규식 `\b(\d{3,5}[A-Z]{0,3}\+?)\b` 은 EPYC 3101 을 잡지만,
다나와가 그 물건을 안 팔면 영원히 빈다.

`enrich_specs_with_price.py` 에 `itmaya_columns()` 가 이미 있다 —
**ITMAYA 색인을 결합한 뒤 채움률을 다시 재야 한다.** 서버급이 채워지면 조달 품목 적중률이 크게 달라진다.
현재 수치만 보고 "가격 DB 가 반쯤 비었다"고 판단하면 안 된다.

### P-3 — 파싱 실패와 0건을 구분하지 않는다 🔴

`parse_products()` 는 다나와 HTML 클래스(`prod_item` · `price_sect` · `spec_list`)에 결합돼 있다.
사이트가 개편되면 **조용히 빈 리스트**를 반환한다. 이건 "가격 없음"과 구분되지 않는다.

`docs/failure-modes.md` 의 언어로는 서로 다른 두 가지다:

- 검색은 됐는데 결과가 0건 → `{ok, result: null}` 또는 `{degrade}`
- 파싱이 깨졌다 → `{fatal, code: "PRICE_SOURCE_BROKEN"}` — 사람이 고쳐야 한다

지금은 둘 다 `[]` 다. **최소한 "응답 HTML 은 왔는데 `li.prod_item` 이 0개"를 이상 신호로 잡아야 한다.**

### P-4 — 페이지 예산을 데드라인에 묶는다

`DEFAULT_DELAY_SECONDS = 0.8` × 페이지 수 + 응답 시간. `pages=3` 이면 하한이 2.4초다.
`MAX_PAGES_HARD_LIMIT = 200` 은 배치 수집용 상한이지 요청 처리용이 아니다.

요청 경로에서는 **남은 데드라인으로부터 `pages` 를 역산**해 강제한다. 상수로 두지 않는다.

### P-5 — 스로틀·robots·ToS

0.8초 간격은 예의 있는 값이다. 다만 조달 사업 납품물의 일부가 될 경우
다나와·ITMAYA 의 이용약관상 위치를 확인해야 한다. **기술 문제가 아니라 계약 문제다.**
확인 전까지는 이 경로를 기본값으로 켜지 않는 편이 안전하다.

### P-6 — 신선도 판정 부재

`collected_at` / `price_checked_at` 을 기록만 하고 읽지 않는다. §4.2 에서 정한 기준을 구현한다.

---

## 6. 이 문서가 요구하는 결정

| # | 결정할 것 | 막고 있는 것 | 주체 | 상태 |
|---|---|---|---|---|
| 1 | CompoPrice 를 벤더링할까, 별도 프로세스로 둘까 | 파일 배치·`CLAUDE.md §3` 표면 목록 | 우리 | 미정 |
| 2 | `/api/price/url` 의 세 갈래 중 하나 (§4.5) | 표면 하나의 존폐 | **백엔드 합의** | 미정 |
| ~~3~~ | ~~인력·용역 가격원~~ | — | — | **확정 — 후순위 (§0.1)** |
| 4 | stale 기준(7일?)과 stale 응답 정책 | §4.2 구현 | 우리 | 미정 |
| 5 | `queryRelaxed` 의 정확한 스키마 (§4.3) | 응답 계약 | **백엔드 합의** | 미정 |
| 6 | 가격 데이터 갱신 주기와 소유자 | §3.1 경계 | 운영 | 미정 |

2·5 는 `decisions.md §3` 으로 올린다. 1·4 는 우리가 정하고 결과만 기록한다.

백엔드 개발자가 맥락 없이 읽을 수 있는 변경 명세는 **[backend-price-api.md](backend-price-api.md)** 에 따로 있다.
이 문서는 우리 쪽 설계 근거이고, 그쪽은 그들이 고쳐야 할 것만 담는다.

---

## 7. 정리 작업 (studyweb 잔재)

이식과 무관하게 지금 할 수 있는 것.

| 위치 | 조치 |
|---|---|
| `app/config.py:46-48` | `SEARCH_PROVIDER` · `STUDYWEB_URL` · `STUDYWEB_API_KEY` 기본값 제거 |
| `app/config.py:24-26` | `DEFAULT_PLATFORMS` 폐기 (다나와가 목록에 없다) |
| `app/config.py:17-21` | `FIELDS` 에서 `searchProvider`·`searchUrl`·`searchKey` 제거 — **`data/ai-config.json` 에 이미 저장된 값이 코드 기본값을 이기므로 마이그레이션이 필요하다** |
| `app/main.py:224` | `/api/price/resolve` 501 사유문에서 studyweb 언급 제거 |
| `.env.example:27-29` | 세 변수 제거 |
| `docs/ai-boundary.md:35` | `price-web.js \| studyweb + LLM 가격 추출` → 실제 구성으로 갱신. **백엔드 사본과 함께 고쳐야 한다** |
| `app/main.py:111-114` | `GET /api/ai/config` 가 `searchProvider`·`searchUrl` 을 노출한다. 계약 §5 의 11개에 없는 12번째 표면 — 존폐를 함께 결정한다 |
