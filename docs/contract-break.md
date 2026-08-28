# 계약 파기 — 프론트·백에 넘기는 철거 작업지시서

2026-08-26 에 이 저장소가 **호출자 계약 6건을 끊었다.** 끊은 쪽이 적는다.

`PORTING_STATUS.md` 는 "이 저장소에서 무엇이 되나"를 적고,
이 문서는 **"다른 저장소가 무엇을 철거해야 하나"**를 적는다.

- 백엔드: `g2bmaster-backend` (Spring Boot / Java)
- 프론트: `g2bmaster-frontend` (React / TypeScript)

---

## §0 방침 — 가격 기능 전체 철거

AI 추정만 걷는 게 아니다. **가격이라는 축 자체를 제품에서 뺀다.**
백엔드의 단가 DB(`price_catalog`)와 딜분석(`deal_analysis`), 프론트의 가격분석·단가DB
화면까지 전부 내린다.

**제품에 남는 AI 기능은 `POST /api/notice-summary` 하나다.**

---

## §1 끊어진 계약

| 사라진 경로 | 대체 | 지금 부르면 |
|---|---|---|
| `POST /api/item-summary` | `POST /api/notice-summary` | 404 → `400 BAD_REQUEST` |
| `POST /api/bid-summary` | `POST /api/notice-summary` | 〃 |
| `POST /api/price/resolve` | **없음 (폐기)** | 〃 |
| `POST /api/price/url` | **없음 (폐기)** | 〃 |
| `POST /api/estimate-unit-cost` | **없음 (폐기)** | 〃 |
| `POST /api/prebuilt-comparables` | **없음 (폐기)** | 〃 |

404 가 `BAD_REQUEST` 로 나가는 이유: 우리가 소유하지 않는 경로는 호출자 문제이므로
`retryable=false` 여야 한다. 재시도해도 결과가 같다 (`app/main.py` 의
`http_exception_handler`).

**살아 있는 경로**

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

---

## §2 `POST /api/notice-summary` 계약

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

`title` 과 `bidNtceNo` 가 **둘 다** 비면 `400 BAD_REQUEST`.
`agency`·`amount`·`documents` 는 선택이다.

**응답 200**

```json
{
  "summary": "- 무엇을 사는가: ...\n- 규모·수량: ...",
  "promptVersion": "notice-summary-2026-08-28-v3",
  "llmModel": "..."
}
```

`summary` 는 **마크다운 불릿 문자열 하나**다.

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
> `scripts/test_http_contract.py` 가 이 불변식을 건다.

---

## §3 백엔드가 철거할 것 (`g2bmaster-backend`)

### AI 계약 맞추기

| # | 위치 | 할 일 |
|---|---|---|
| 1 | `integration/ai/AiClient.java:56` | `itemSummary()` → `noticeSummary()`, 경로를 `/api/notice-summary` 로 |
| 2 | `integration/ai/AiClient.java:68` | `bidSummary()` 삭제 |
| 3 | `integration/ai/AiClient.java:89,99,107,112` | `resolvePrice`·`estimateUnitCost`·`prebuiltComparables`·`resolvePriceByUrl` 삭제 |
| 4 | `analysis/AnalysisJobRunner.java:159` | 호출부 교체. 응답이 `summary` 하나뿐이므로 `saveHistory()` 가 facts/items 를 기대하면 함께 손본다 |

> **1번의 `aiDisabled`/`aiFallback` 가드는 제거하지 말 것.** 이제 AI 쪽이 지어낸 200 을
> 내지 않으므로 논리적으로는 불필요하지만, 방어선을 지우면 같은 회귀가 다시 조용해진다.

### 가격 기능 철거

| # | 대상 | 비고 |
|---|---|---|
| 5 | `pricing/` 패키지 전체 | `PriceCatalogController`·`PriceCatalogService`·`PriceCatalogRepository`·`PriceCatalogRequests`·`MarketPriceService`·`UnitCostValidator`·`DealAnalysisService`·`DealAnalysisRepository` |
| 6 | `market/MarketIntelController.java` | 가격 갈래만 — `/deal-analysis`, `/deal-analysis/backfill`, `/prebuilt-comparables` |
| 7 | HTTP 경로 | `/api/price-catalog` 및 하위 `/{id}`·`/history`·`/ingest` |
| 8 | `src/test` | `*Price*`·`*Deal*`·`*Market*` 테스트 정리 |

> **6번 주의.** `MarketIntelController` 의 `/bid-opening-results`·`/collusion-analysis`·
> `/company-history`·`/officer-search` 는 가격이 아니다. **남긴다.**

### 설정 표면

| # | 위치 | 할 일 |
|---|---|---|
| 9 | `system/SystemStatusService.java` | `GET /api/ai/config` 응답에서 `searchProvider`·`searchUrl`·`searchPlatforms`·`pricePrompt`·`searchKey`·`searchKeySet` 가 사라졌다 |
| 10 | — | **AI 설정 쓰기 경로가 없다.** `set_ai_config()` 를 지웠다. 설정은 읽기 전용이며 `.env` 로만 바뀐다. 저장 프록시를 만들 계획이었다면 폐기 |

### DB

> **적용된 마이그레이션을 삭제하지 마라.** `V9__deal_analysis_result.sql`,
> `V10__price_catalog.sql`, `V11__bid_notice_estimated_price.sql`,
> `V20260814132535__bid_notice_margin_rate.sql` 을 지우면 Flyway 체크섬이 깨져
> **기동 자체가 실패한다.** 테이블은 **새 drop 마이그레이션을 추가**해서 내린다.

### 배포 전에 확인할 것

- **프롬프트 버전이 바뀐다.** `AiClient.promptVersion()` 이 돌려주는 값이
  `item-summary-2026-08-04-v4` → `notice-summary-2026-08-28-v3` 가 된다.
  `analysis_history.prompt_version` / `analysis_jobs.prompt_version` 재사용 키가 전부
  어긋나 **기존 분석이 자동 무효화**된다. 의도된 동작이지만 대량 재분석이 큐에 몰릴 수 있다.
- **`margin_rate` 파급.** `V20260814132535` 로 마진율이 **색인의 축**으로 올라가 있다.
  가격을 걷으면 검색 패싯·정렬이 함께 흔들린다. **철거 전에 색인 의존을 먼저 끊을 것.**

---

## §4 프론트가 철거할 것 (`g2bmaster-frontend`)

> 백엔드가 노출할 요약 경로가 정해진 뒤 착수한다. 먼저 지우면 붙일 곳을 잃는다.

### 파일 통째 삭제

- `src/api/price.ts`, `src/api/pricing.ts`
- `src/features/price/` — `PriceDbTable.tsx`, `priceDbRows.ts`, `priceDbRows.test.ts`
- `src/features/deal/` — `PriceTable.tsx`, `priceRows.ts`, `deal.css`
- `src/features/notices/drawer/PriceAnalysisPanel.tsx`
- `src/routes/PriceDatabaseScreen.tsx`, `src/routes/priceDb.test.tsx`
- `src/routes/DealRadarScreen.tsx`, `src/routes/DealRadarScreen.test.tsx`
- `src/domain/priceStatus.ts`

### 부분 수정

| 위치 | 할 일 |
|---|---|
| `src/routes/routePaths.ts` · `router.tsx` | 단가DB·딜레이더 경로와 네비 항목 제거 — 경로만 지우고 화면을 남기면 죽은 코드가 된다 |
| `src/api/analysis.ts:309,389` | `bidSummary()`·`itemSummary()` 를 단일 요약 경로로 통합. 응답 타입을 `{summary, promptVersion, llmModel}` 로 좁힌다 |
| `src/api/analysis.ts:553` | `prebuiltComparables()` 삭제 |
| `src/api/analysis.ts:27,60,96,104,111,115,117,170,180` | `unitCost`·`estimatedUnitCost`·`prebuilt`·`priceSource` 타입 삭제 |
| `src/api/config.ts:28-38` | `MaskedAiConfig` 에서 `searchProvider`·`searchUrl`·`searchPlatforms`·`pricePrompt`·`searchKey`·`searchKeySet` 제거 |
| `src/api/config.ts:41-48` | `AiConfigResponse.status.search` 제거 |
| `src/api/config.ts:54-71` | `AiConfigUpdate`·`updateAiConfig()` — 쓰기 경로가 없어졌다 (§3-10) |
| `src/api/index.ts` | `export * from './price'` 제거 |
| `src/features/notices/drawer/IndexNoticeDrawer.tsx` (+ `.test.tsx:46,174`) | 가격 패널 연결 해제 |
| `src/features/notices/IndexCell.tsx` · `indexRows.ts` | 가격·마진 컬럼 정리 |
| `src/domain/columns.ts` · `storage.ts` | 저장된 컬럼 설정에서 가격 컬럼 제거 — 남기면 옛 설정이 살아 있는 사용자 화면이 깨진다 |
| `src/api/analysis.test.ts` · `routes/NoticeSearchScreen.test.tsx` | 가격 픽스처 제거 |
| `src/features/beta/landing.config.ts` · `components/ProductMock.tsx` | 가격 기능을 광고하는 문구가 있으면 함께 내린다 |

---

## §5 순서

가격을 먼저 걷고, 그 다음 요약을 붙인다. 반대로 하면 요약을 붙이는 동안
죽은 가격 코드가 계속 컴파일을 막는다.

1. **백엔드** — §3 색인 의존 끊기 → 가격 철거(5~8) → drop 마이그레이션
2. **백엔드** — §3 AI 계약(1~4), 요약 경로 확정
3. **프론트** — §4 파일 삭제 → 라우트·네비 정리 → 요약 경로 연결
4. 양쪽 테스트 초록 확인 후 `promptVersion` 무효화 영향(재분석 큐) 관찰
