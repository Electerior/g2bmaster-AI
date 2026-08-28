# 계약 파기 — 프론트·백에 넘기는 철거 작업지시서

2026-08-26 에 이 저장소가 **호출자 계약 6건을 끊었다.** 끊은 쪽이 적는다.

`PORTING_STATUS.md` 는 "이 저장소에서 무엇이 되나"를 적고,
이 문서는 **"다른 저장소가 무엇을 철거해야 하나"**를 적는다.

- 백엔드: `g2bmaster-backend` (Spring Boot / Java)
- 프론트: `g2bmaster-frontend` (React / TypeScript)

---

## §0 이 작업을 맡는 사람·에이전트에게

### 범위 규칙 — 이것만 지키면 된다

1. **이 문서에 적힌 것만 한다.** 여기 없는 파일은 열지도 고치지도 않는다.
2. **없애는 작업이지 만드는 작업이 아니다.** 새 기능·새 추상화·새 헬퍼를 끼워 넣지
   않는다. 대체 구현이 필요해 보이면 그것은 이 작업이 아니다 — §7 에 적고 멈춘다.
3. **"하는 김에" 리팩터링하지 않는다.** 포매팅·네이밍·import 정리·주석 손질을
   섞지 마라. 철거 diff 에 그런 것이 섞이면 리뷰어가 무엇이 의도된 삭제인지
   구분하지 못한다.
4. **이름이 비슷하다고 지우지 않는다.** `market/` 패키지처럼 가격과 무관한 것이
   같은 자리에 있다. §6 의 "건드리지 말 것"을 먼저 읽어라.
5. **판단이 필요하면 멈추고 묻는다.** §7 에 열린 질문 3개가 있다. 임의로 정하지
   마라 — 그 셋은 제품 결정이지 구현 선택이 아니다.

### 행 번호를 쓰지 않는 이유

이 문서는 **심볼 이름**으로 위치를 가리킨다. 행 번호는 저장소가 커밋을 쌓는 순간
썩는다. 각 항목에 `grep` 명령을 함께 적었으니 그대로 실행해 현재 위치를 찾아라.

### 끝났는지 확인하는 법

§8 에 저장소별 검증 명령이 있다. **그것이 초록이면 끝이고, 아니면 안 끝났다.**
"대충 다 지운 것 같다"로 끝내지 마라.

---

## §1 방침 — 가격 기능 전체 철거

AI 추정만 걷는 게 아니다. **가격이라는 축 자체를 제품에서 뺀다.**
백엔드의 단가 DB(`price_catalog`)와 딜분석(`deal_analysis`), 프론트의 가격분석·단가DB
화면까지 전부 내린다.

**제품에 남는 AI 기능은 `POST /api/notice-summary` 하나다.**

되살릴 계획이 없다. "나중에 쓸지도 모르니 주석 처리"하지 말고 지워라 —
지운 것은 git 히스토리에 남는다.

---

## §2 끊어진 계약

| 사라진 경로 | 대체 | 지금 부르면 |
|---|---|---|
| `POST /api/item-summary` | `POST /api/notice-summary` | 404 → `400 BAD_REQUEST` |
| `POST /api/bid-summary` | `POST /api/notice-summary` | 〃 |
| `POST /api/price/resolve` | **없음 (폐기)** | 〃 |
| `POST /api/price/url` | **없음 (폐기)** | 〃 |
| `POST /api/estimate-unit-cost` | **없음 (폐기)** | 〃 |
| `POST /api/prebuilt-comparables` | **없음 (폐기)** | 〃 |

404 가 `BAD_REQUEST` 로 나가는 이유: 우리가 소유하지 않는 경로는 호출자 문제이므로
`retryable=false` 여야 한다. 재시도해도 결과가 같다.

**살아 있는 경로 — 이건 그대로 쓴다**

| | |
|---|---|
| `GET /health` · `/healthz` | 헬스체크 (임베딩 상태 동봉) |
| `GET /api/ai/config` | 설정 조회 — **읽기 전용** |
| `GET /api/ai/prompt-version` | 재사용 키에 들어가는 프롬프트 버전 |
| `GET /api/ai/capacity` | 워커 용량 (내보내기 ETA 계산용) |
| `GET /api/llm/models` | 모델 목록·도달 여부 |
| `POST /api/embed` | 텍스트 임베딩 (유사도 계산은 백엔드가 한다) |
| `POST /api/notice-summary` | **공고 요약 — 유일하게 남은 추론 표면** |

여전히 `501 NOT_PORTED` 인 3개: `/api/legal/review-clauses`,
`/api/legal/outreach-draft`, `/api/pledge/revision-workflow`.
**이 셋은 이번 작업 대상이 아니다.** 호출부를 지우지 마라 — 나중에 이식한다.

---

## §3 `POST /api/notice-summary` 계약

**요청**

```json
{
  "bidNtceNo": "20260812345-00",
  "title": "정보시스템 서버 및 스토리지 장비 구매",
  "agency": "조달청",
  "amount": 120000000,
  "documents": [{ "name": "규격서.hwp", "text": "1. 사업개요 ..." }]
}
```

`title` 과 `bidNtceNo` 는 **둘 중 하나만 있어도** 되고, 둘 다 비면 `400 BAD_REQUEST`.
`agency`·`amount`·`documents` 는 선택이다.

**응답 200**

```json
{
  "summary": "- 무엇을 사는가: ...\n- 예산·금액: ...",
  "promptVersion": "notice-summary-2026-08-28-v3",
  "llmModel": "..."
}
```

`summary` 는 **마크다운 불릿 문자열 하나**다. 항목명과 순서는 프롬프트가 고정한다
(무엇을 사는가 / 예산·금액 / 규모·수량·핵심 규격 / 기간 / 참가자격 / 주의할 조건).

**없어진 것.** 사실추출(`facts`)·품목추출(`items`)·원가추정 필드는 전부 사라졌다.
원본의 4스텝(clamp → facts → summary → items)을 LLM 호출 한 번으로 줄였다.

**실패**

| 코드 | status | retryable |
|---|---|---|
| `BAD_REQUEST` | 400 | ✗ |
| `LLM_UNAVAILABLE` | 503 | ✓ |
| `LLM_TIMEOUT` | 504 | ✓ |
| `LLM_MALFORMED` | 502 | ✓ |

본문 모양은 `docs/failure-modes.md` 를 따른다.

> **200 에 지어낸 요약은 실리지 않는다.** 한때 `item-summary`·`bid-summary` 는
> payload 에 이름만 있으면 `"…요약이 완료되었습니다"` 를 `aiFallback=false` 로 실어
> 200 을 냈다. `AiClient` 는 `aiDisabled`/`aiFallback` 만 거르므로 그것을 성공으로
> 판정하고, `AnalysisJobRunner` 가 `analysis_history` 에 적재한 뒤 작업을 완료
> 처리한다 — 재사용 키가 같으므로 그 행은 **영원히 재분석되지 않는다.**
> 지금은 LLM 이 닿지 않으면 503 이고, 빈 요약도 502 다.

---

## §4 백엔드가 할 일 (`g2bmaster-backend`)

### 4-1. AI 계약 맞추기

| 심볼 | 할 일 |
|---|---|
| `AiClient#itemSummary` | `noticeSummary` 로 이름을 바꾸고 경로를 `/api/notice-summary` 로 |
| `AiClient#bidSummary` | 삭제 |
| `AiClient#resolvePrice` | 삭제 |
| `AiClient#estimateUnitCost` | 삭제 |
| `AiClient#prebuiltComparables` | 삭제 |
| `AiClient#resolvePriceByUrl` | 삭제 |
| `AnalysisJobRunner#execute` | `aiClient.itemSummary(payload)` 호출부를 교체 |
| `AnalysisJobRunner#saveHistory` | 응답이 `summary` 하나뿐이다. facts/items 를 꺼내 쓰고 있으면 함께 손본다 |

```bash
grep -rn 'itemSummary\|bidSummary\|resolvePrice\|estimateUnitCost\|prebuiltComparables' \
  src/main/java --include='*.java'
```

> **`aiDisabled`/`aiFallback` 가드는 제거하지 마라.** 이제 AI 쪽이 지어낸 200 을
> 내지 않으므로 논리적으로는 불필요하다. 그래도 남긴다 — 방어선을 지우면 같은
> 회귀가 다시 조용해진다. **이건 "죽은 코드 정리" 대상이 아니다.**

### 4-2. 가격 기능 철거

**패키지 통째로 삭제** — `com.electerior.g2bmaster.pricing` 의 **9개 전부**

`PriceCatalogController` · `PriceCatalogService` · `PriceCatalogRepository` ·
`PriceCatalogRequests` · `MarketPriceService` · `UnitCostValidator` ·
`DealAnalysisService` · `DealAnalysisRepository` · `DealCalculator`

> **`DealCalculator` 는 마지막에 지운다.** `MarketIntelController`·
> `DealAnalysisService`·`UnitCostValidator` 세 곳이 쓴다. 먼저 지우면 컴파일이
> 세 군데서 깨져 원인을 찾느라 시간을 버린다.

**컨트롤러에서 경로만 삭제** — `MarketIntelController`

| 경로 | |
|---|---|
| `/deal-analysis` | 삭제 |
| `/deal-analysis/backfill` | 삭제 |
| `/prebuilt-comparables` | 삭제 |
| `/api/price-catalog` (+ `/{id}` · `/history` · `/ingest`) | `PriceCatalogController` 와 함께 사라진다 |

```bash
grep -rn 'deal-analysis\|prebuilt-comparables\|price-catalog' src/main/java --include='*.java'
```

**테스트 9개** (`src/test/java/com/electerior/g2bmaster/`)

| 파일 | 처리 |
|---|---|
| `pricing/PriceCatalogServiceTest` · `PriceCatalogRepositoryTest` | 삭제 (대상 클래스가 사라진다) |
| `pricing/DealAnalysisServiceTest` · `DealCalculatorTest` | 삭제 |
| `pricing/MarketPriceServiceTest` · `UnitCostValidatorTest` · `AwardKeywordTest` | 삭제 |
| `market/MarketIntelControllerTest` | **삭제하지 말 것.** 가격 경로 3개를 거는 케이스만 뺀다 |
| `config/OpenApiDocumentTest` | **삭제하지 말 것.** 경로 목록 스냅샷이라 기대값만 갱신한다 |

### 4-3. 설정 표면

| 심볼 | 할 일 |
|---|---|
| `SystemStatusService` | `GET /api/ai/config` 응답에서 `searchProvider`·`searchUrl`·`searchPlatforms`·`pricePrompt`·`searchKey`·`searchKeySet` 가 사라졌다. 읽고 있으면 제거 |

**AI 설정 쓰기 경로는 없다.** 우리가 `set_ai_config()` 를 지웠다. AI 설정은 읽기
전용이며 `.env` 로만 바뀐다. 저장 프록시를 만들 계획이었다면 폐기하라.
**여기서 "설정 저장 API 를 백엔드에 새로 만들자"로 가지 마라** — §0 규칙 2 위반이다.

### 4-4. DB

> **적용된 마이그레이션을 삭제하지 마라.** `V9__deal_analysis_result.sql`,
> `V10__price_catalog.sql`, `V11__bid_notice_estimated_price.sql`,
> `V20260814132535__bid_notice_margin_rate.sql` 을 지우면 Flyway 체크섬이 깨져
> **기동 자체가 실패한다.** 테이블은 **새 drop 마이그레이션을 추가**해서 내린다.

---

## §5 프론트가 할 일 (`g2bmaster-frontend`)

> **백엔드가 요약 경로를 확정한 뒤 시작한다.** 먼저 지우면 붙일 곳을 잃는다.

### 5-1. 파일 통째 삭제

```
src/api/price.ts
src/api/pricing.ts
src/features/price/          (PriceDbTable.tsx · priceDbRows.ts · priceDbRows.test.ts)
src/features/deal/           (PriceTable.tsx · priceRows.ts · deal.css)
src/features/notices/drawer/PriceAnalysisPanel.tsx
src/routes/PriceDatabaseScreen.tsx
src/routes/priceDb.test.tsx
src/routes/DealRadarScreen.tsx
src/routes/DealRadarScreen.test.tsx
src/domain/priceStatus.ts
```

### 5-2. 부분 수정

| 위치 | 심볼 | 할 일 |
|---|---|---|
| `src/api/index.ts` | `export * from './price'` · `export * from './pricing'` | **둘 다** 제거 |
| `src/api/analysis.ts` | `summarizeBid` · `summarizeItem` (+ 훅 `useBidSummary` · `useItemSummary`) | 백엔드가 정한 단일 요약 경로로 통합. 응답 타입을 `{summary, promptVersion, llmModel}` 로 좁힌다 |
| `src/api/analysis.ts` | `fetchPrebuiltComparables` (+ 훅 `usePrebuiltComparables`) | 삭제 |
| `src/api/analysis.ts` | `analyzeDeal` (+ 훅 `useDealAnalysis`) | 딜분석 자체가 폐기다. 삭제 — 백엔드 `/deal-analysis` 가 사라진다 |
| `src/api/analysis.ts` | `unitCost` · `estimatedUnitCost` · `prebuilt` · `priceSource` · `unitCostSource` | 타입 삭제 |
| `src/api/analysis.ts` | `'deal-radar'` 분기 | 프롬프트를 바꾸고 품목 분해 필드를 붙이던 갈래. 삭제 |
| `src/api/config.ts` | `MaskedAiConfig` | `searchProvider`·`searchUrl`·`searchPlatforms`·`pricePrompt`·`searchKey`·`searchKeySet` 필드 제거 |
| `src/api/config.ts` | `AiConfigResponse` | `status.search` 제거 |
| `src/api/config.ts` | `AiConfigUpdate` · `updateAiConfig` | 쓰기 경로가 없어졌다 (§4-3) |
| `src/domain/columns.ts` | `'deal-radar'` · `'price-db'` | **화면 종류 정의 자체.** 유니온 타입과 `SCREENS` 맵 양쪽에 있다. 여기를 안 지우면 라우트를 지워도 타입에 유령이 남는다 |
| `src/domain/storage.ts` | 저장된 컬럼 설정 | 가격 컬럼 제거 — 남기면 옛 설정이 살아 있는 사용자 화면이 깨진다 |
| `src/routes/routePaths.ts` | `ROUTES.dealRadar` · `ROUTES.priceDb` | 경로 상수와 네비 항목 |
| `src/routes/router.tsx` | `DealRadarScreen` · `PriceDatabaseScreen` | import 와 `<Route>` 등록 |
| `src/features/notices/drawer/IndexNoticeDrawer.tsx` | 가격 패널 | 연결 해제 (+ 같은 이름 `.test.tsx` 픽스처) |
| `src/features/notices/IndexCell.tsx` · `indexRows.ts` | 가격·마진 컬럼 | 렌더링 정리 |
| `src/api/analysis.test.ts` · `src/routes/NoticeSearchScreen.test.tsx` | 가격 픽스처 | 제거 |
| `src/features/beta/landing.config.ts` · `components/ProductMock.tsx` | 마케팅 문구 | 가격 기능을 광고하고 있으면 내린다 |

> **함수 이름이 경로 이름과 다르다.** `/api/bid-summary` 를 부르는 함수는 `bidSummary`
> 가 아니라 **`summarizeBid`** 다. 경로 문자열로 먼저 찾고 함수명을 확인하라.

```bash
grep -rn "deal-radar\|price-db\|estimatedUnitCost\|prebuilt\|pricePrompt" src \
  --include='*.ts' --include='*.tsx'
grep -rn "'/api/bid-summary'\|'/api/item-summary'\|'/api/deal-analysis'" src
```

---

## §6 건드리지 말 것

**이름이 비슷하다는 이유로 지우면 멀쩡한 기능이 죽는다.** 아래는 확인한 것이다.

| 대상 | 왜 남기나 |
|---|---|
| `market/MarketIntelService.java` · `MarketIntelRequests.java` | **가격 참조가 0건이다**(확인함). `market/` 에서 걷을 것은 컨트롤러의 세 경로뿐이다 |
| `MarketIntelController` 의 `/bid-opening-results` · `/collusion-analysis` · `/company-history` · `/officer-search` | 가격이 아니다. 시장분석·업체이력 기능이다 |
| `AiClient` 의 `aiDisabled`/`aiFallback` 가드 | §4-1 참고. 방어선이다 |
| `AiClient#embed` · `#promptVersion` · `#capacity` · `#models` | 살아 있는 계약이다 (§2) |
| `/api/legal/*` · `/api/pledge/*` 호출부 | 아직 `501 NOT_PORTED` 일 뿐 폐기가 아니다. 나중에 이식한다 |
| 적용된 Flyway 마이그레이션 파일 | §4-4. 지우면 기동이 실패한다 |
| `src/api/specs.ts` · `legal.ts` · `export.ts` 등 | 가격과 무관하다 |

---

## §7 멈추고 물어야 할 것

임의로 정하지 마라. **제품 결정이지 구현 선택이 아니다.**

1. **`margin_rate` 를 어떻게 할 것인가.** `V20260814132535` 로 마진율이 **검색 색인의
   축**으로 올라가 있다(`BidNoticeMarginSqlTest` 가 건다). 가격을 걷으면 검색
   패싯·정렬이 함께 흔들린다. 마진을 남길지, 색인에서 뺄지 먼저 정해야 한다.
   **정하기 전에는 `index/` 를 건드리지 마라.**
2. **`analysis_history` 재분석 폭주를 감당할 것인가.** `AiClient#promptVersion` 이
   돌려주는 값이 `item-summary-2026-08-04-v4` → `notice-summary-2026-08-28-v3` 로
   바뀐다. 재사용 키가 전부 어긋나 **기존 분석이 자동 무효화**된다. 의도된
   동작이지만 배포 직후 대량 재분석이 큐에 몰릴 수 있다.
3. **요약 경로를 백엔드가 어떤 이름으로 노출할 것인가.** 프론트 작업(§5)이 여기에
   묶여 있다. `/api/notice-summary` 를 그대로 프록시할지, 기존 이름을 유지할지.

---

## §8 순서와 검증

가격을 먼저 걷고 요약을 붙인다. 반대로 하면 요약을 붙이는 동안 죽은 가격 코드가
계속 컴파일을 막는다.

| # | 저장소 | 일 |
|---|---|---|
| 1 | 백엔드 | §7-1 결정 → 색인의 마진 의존 정리 |
| 2 | 백엔드 | §4-2 가격 철거 (`DealCalculator` 는 마지막) → drop 마이그레이션 |
| 3 | 백엔드 | §4-1 AI 계약, §4-3 설정. 요약 경로 확정 |
| 4 | 프론트 | §5-1 삭제 → §5-2 라우트·타입 정리 → 요약 경로 연결 |
| 5 | 양쪽 | 아래 검증 초록 확인 → §7-2 재분석 큐 관찰 |

**백엔드**

```bash
./mvnw clean test                    # 초록이어야 한다
grep -rn 'item-summary\|bid-summary\|price/resolve\|price/url\|estimate-unit-cost\|prebuilt-comparables' \
  src/main/java --include='*.java'   # 0줄
```

**프론트**

```bash
npm run build                        # 타입 에러 0
npm test
grep -rn "'/api/price\|item-summary\|bid-summary\|prebuilt-comparables" src   # 0줄
```

**양쪽 붙여서**

```bash
# AI 서비스를 띄우고(포트는 g2bmaster-AI 의 .env PORT)
cd g2bmaster-AI && make start &
cd ../g2bmaster-backend && AI_ENABLED=true AI_BASE_URL=http://localhost:8000 ./mvnw spring-boot:run
```

공고 1건을 분석 큐에 넣어 `analysis_history` 에 행이 쌓이는지, 그리고 **AI 를 끈 채로**
같은 요청이 폴백 200 이 아니라 분류된 실패로 처리되는지 확인한다.

AI 저장소 쪽 확인은 `g2bmaster-AI/README.md` 「검증 — 직접 돌리는 법」에 있다.
