# API 계약 — g2bmaster-backend

원본 모놀리스 `g2bmastersopen/server.js` (5363줄) 의 **65개 라우트 + WebSocket 1개**를
이식 기준으로 정리한 것이다. 프론트(`g2bmaster-frontend`)와의 계약이므로,
필드명을 바꾸면 두 저장소가 동시에 깨진다.

---

## 1. 전역 규약

### 1.1 응답 봉투 — 통일된 봉투는 없다 (의도적)

원본은 네 가지 모양을 쓰고, 프론트가 그에 맞춰져 있다. 이식하면서 통일하지 않는다.

1. **페이징 검색** — `{ items, totalCount, pageNo, numOfRows }`
   `pageNo=0` 은 "페이징 없이 전부". 검색 계열은 `_cached: boolean` 을 덧붙이고,
   일부는 `sourceCounts`·`sourceErrors`·`sourceStatus`·`_analysisQueue` 를 더한다.
2. **오류** — 항상 `{ error: "한국어 메시지" }`. 일부는 `code`, `missing[]` 추가.
   상태코드 400/401/403/404/409/410/413/500/502/503.
3. **단순 확인** — `{ ok: true, ... }`
4. **AI 분석** — 봉투 없이 큰 평평한 객체. **LLM 실패도 HTTP 200** 에
   `aiFallback: true` + `aiError` 로 내려간다. 의도된 계약이다(→ `docs/ai-boundary.md` §6.4).

**한국어 오류 문구는 사용자 대상 계약**이다. 화면에 그대로 렌더링되므로 임의로 바꾸지 않는다.

### 1.2 인증 세 갈래

| 방식 | 헤더 | 적용 |
|---|---|---|
| 앱 API 키 | `Authorization: Bearer <key>` 또는 `X-API-Key: <key>` | 쓰기·비용 발생 경로. **`APP_API_KEY` 미설정 시 인증 꺼짐**(개발 모드) |
| 디버그 비밀값 | `X-Debug-Secret` | 운영에서만. 비밀값 자체가 없으면 401 이 아니라 **404**(경로 은닉) |
| 알림 비밀값 | `X-Alert-Secret` | `POST /api/run-alert` 전용 |

Java 에서는 `@RequireAppAuth` 애너테이션 + `AppAuthInterceptor`,
그리고 `DebugAccessGuard.check(request)` 로 대체했다.

### 1.3 검색 공통 질의 파라미터

| 파라미터 | 의미 |
|---|---|
| `andTerms` / `orTerms` / `notTerms` | 공백·콤마 구분 |
| `pageNo` (기본 1) | `0` 이면 전부 |
| `perPage` (또는 `'all'`) | 1..500 로 클램프, `'all'` → 99999 |
| `fromDate` / `toDate` | `YYYY-MM-DD` → `YYYYMMDDHHmm`. 기본값 최근 7일 |
| `insttNm` | 발주기관 |
| `sortKey` / `sortDir` | `asc\|desc` |
| `searchField=item` | 품목 모드 |
| `bidType` | `물품\|용역\|공사` |
| `simOr` / `simFile` | 임베딩 유사도 재정렬 (AI 필요) |

---

## 2. 컨텍스트별 라우트

### A. 입찰 검색 (4)

| 라우트 | 비고 |
|---|---|
| `GET /api/bid-announce` | 나라장터 물품/용역/공사 + D2B + 누리장터 팬아웃. 추가 파라미터 `activeOnly`, `bidNtceNo`(콤마, 최대 5), `fileScan`, `simOr`. 응답에 `sourceCounts`/`sourceCoverage`/`sourceErrors`/`sourceStatus`/`_analysisQueue` |
| `GET /api/bid-result` | `ScsbidInfoService`. 추가 `corpNm`. `bidNtceNo` 로 중복 제거 |
| `GET /api/bid-plan` | `PrcrmntReqInfoService`. 엔티티 타입 `procurement_request` |
| `GET /api/pre-spec` | `HrcspSsstndrdInfoService`. DB 우선/카나리/API 소스 선택(`PRE_SPEC_SOURCE`). 엔티티 타입 `pre_spec` |

공고 항목에는 원본 G2B 필드 위에 이식 계층이 얹는 필드가 붙는다:
`_type`, `_source`(`g2b|d2b|private-g2b`), `_sourceLabel`, `_isCancelled`,
`_contractSummary`, `_requirementSummary`, `_opportunityScore`(0~100),
`_opportunityGrade`(`S|A|B|C|X`), `_opportunityAction`, `_opportunityReasons[]`,
`_opportunitySummary`, `_simScore`, `_analysis: {status, error?}`.

### B. 트렌드 (3)

`GET /api/trends/product`, `/api/trends/service`, `/api/trends/construction`

응답: `{ period, summary{totalCount,totalAmount,averageAmount,medianAmount,todayCount,closingSoonCount}, byDay[], topInstitutions[], contractMethods[], keywords[], closingSoon[], highValue[], _cached }`

각 트렌드는 12개 키워드 그룹을 가지며, 사용자가 `AI` 를 치면 그 그룹의 키워드 목록으로 확장된다.

### C. 시장 정보 (5)

| 라우트 | 요청 | 응답 |
|---|---|---|
| `POST /api/bid-opening-results` | `{bidNtceNo!, bidNtceSqNo?, type?}` | `{bidNtceNo, bidNtceSqNo, participants[]}`. 미공개면 오류가 아니라 `[]` |
| `POST /api/deal-analysis` | `{item, type?, bidPrice?, unitCost?, quantity?, deep?}` | `{score, facts, market, opening, deal, spec, estimatedUnitCost, d2bGw}` — 5갈래 병렬 조회 |
| `POST /api/company-history` | `{corpNm?, brnNo?, fromDate?, toDate?}` (둘 중 하나 필수) | `{wins[], participations[], stats{winCount,participationCount,winRate,avgBidRate,bidRateSeries[]}}` |
| `POST /api/officer-search` | `{insttNm!, fromDate?, toDate?}` | `{officers[{name,tel,email,bids[]}], totalBids}` |
| `POST /api/collusion-analysis` | `{bids[]}` (최대 20건) | `{bids[], pairs, companies}` |

### D. AI 분석 (4) — 전부 AI 저장소로 위임

| 라우트 | 인증 | 비고 |
|---|---|---|
| `POST /api/bid-summary` | — | 첨부 파싱 + LLM 요약 |
| `GET /api/bid-summary` | — | 같은 기능, 질의 파라미터 버전. 프롬프트가 다름 |
| `POST /api/item-summary` | **앱 키** | 가장 큰 핸들러. 재사용 캐시 → 첨부 → 컨텍스트 클램프 → 사실 추출 → 본 요약 → 품목 추출 |
| `POST /api/analysis-jobs/status` | **앱 키** | `{items[]}` 최대 500. 큐 상태 조회 |

모든 AI 응답에는 `documentSignals` 블록(`summary`, `documentTags[]`,
`bidBlockingClauses`, `legalAssessment`)과 파일 결과 블록(`parsedFiles[]`,
`failedFiles[]`, `fileSummary`, `sourceTrace`)이 붙는다.

### E. 첨부·파일 (4)

| 라우트 | 비고 |
|---|---|
| `POST /api/parse-file` | multipart, 20MB. HWP/HWPX/DOCX/XLSX/PDF/ZIP 텍스트 추출 |
| `POST /api/scan-attachments` | 대량 첨부 스캔. 즉시 처리분 + 백그라운드 워밍업 큐 |
| `GET /api/download-attachment` | **바이너리 스트림.** SSRF 가드: http(s) 만, IP 리터럴 금지, 호스트가 `g2b.go.kr\|data.go.kr\|d2b.go.kr\|naramarket.go.kr` 계열이어야 하며 **리다이렉트 홉마다 재검증**(최대 5회) |
| `GET /api/related-attachments/metrics` | **앱 키**. 프로세스 수명 카운터 |

### F. 가격 (3) — AI 저장소 위임

`GET /api/web-price?name=`, `GET /api/web-price-url?url=`, `POST /api/prebuilt-comparables`

> **보존해야 할 규칙**: 규격→모델 해석이 일어났는데 검색이 질의를 완화했다면
> `result` 를 강제로 `null` 로 만든다. 틀린 단가는 없는 단가보다 나쁘다.

### G. 저장 공고 (4) — 전부 앱 키

`POST /api/saved-notices`, `GET /api/saved-notices?q=&limit=`,
`GET /api/saved-notices/:no?ord=`, `DELETE /api/saved-notices/:no?ord=`

복합 PK `(bid_ntce_no, bid_ntce_ord)`. 쓰기 시 파생: `real_estimate = round(amount * 1.1)`,
`search_text` = 제목+기관+요약+메모+견적 품목명 (20만자 절단).

### H. 내보내기 작업 (6) — 전부 앱 키

| 라우트 | 비고 |
|---|---|
| `POST /api/export-jobs` | **202**. `Idempotency-Key` 헤더 |
| `GET /api/export-jobs/:id` | `{job: {...,downloadUrl, canRetryAnalysis}}` |
| `POST /api/export-jobs/:id/retry` | 202 |
| `POST /api/export-jobs/:id/cancel` | 202. `runningCount` 가 0 이 될 때까지 폴링 |
| `GET /api/export-jobs/:id/download` | **파일 다운로드.** 404/409(미완료)/410(만료) + 경로 탈출 가드 |
| `GET /api/export-jobs/:id/items` | `offset`/`limit`(1..500) |

상태: `pending → analyzing → generating → completed` (+ `failed`, `cancelled`).

### I. 법령·서약서 (3) — AI 저장소 위임

`POST /api/legal-outreach`, `POST /api/pledge-revision/upload`(multipart, 앱 키),
`POST /api/pledge-revision`(앱 키)

> 서약서 경로는 **문서 태그가 확인되기 전에는 첨부를 내려받지 않는다**
> (`{status:'tag_missing'}` 로 400). 불필요한 다운로드와 LLM 호출을 막는 가드다.

### J. 하드웨어 스펙 (6) — Python 모듈 서버 프록시, 전부 앱 키

`POST /api/search/titles`, `POST /api/extract/specs`, `GET /api/specs/cpu`,
`GET /api/specs/gpu`, `POST /api/specs/fetch-notices`, `POST /api/specs/search-documents`

연결 실패 시 **502** `{error: 'Module server unavailable: …'}`.

### K. 시스템·운영 (16)

`GET /healthz`, `GET /api/debug`, `GET /api/system/status|operations|calls|tables|backfill|schedules`,
`POST /api/system/backfill|schedules|search-compare`, `DELETE /api/system/backfill|schedules/:id`,
`GET /api/llm/models`, `GET|POST /api/ai-config`, `POST /api/run-alert`

> **이식하면서 고칠 것**: `GET /api/system/tables` 는 `information_schema` 의 테이블명으로
> `UNION ALL` 을 **문자열 연결로** 만든다(인용·이스케이프 없음). SQL 인젝션 형태다.
> Java 에서는 화이트리스트 또는 통계 테이블 조회로 바꾼다.

### L. 디버그 (7)

`GET /api/debug/prcrmnt|correction-alert|corp-test`, `POST /api/debug-item-files`,
`GET /api/debug-g2b`, `GET /api/debug`, `GET /api/debug/d2b`

---

## 3. WebSocket

`ws://…/api/test-dashboard/ws` — Vitest 실행 대시보드. **인증 없음.**

이것은 Node 스택 개발 도구다. Java 는 JUnit + Maven 을 쓰므로 **이식하지 않는다.**

---

## 4. 이식하면서 반드시 살려야 하는 동작

원본 코드에 주석으로 이유가 적혀 있거나, 과거 장애로 추가된 것들이다.

1. **나라장터 동시 호출 제한 4** (`G2B_MAX_CONCURRENCY`) — 없애면 429 폭풍이 재현된다.
2. **날짜 창 이분 분할** — 범위 오류·타임아웃·6페이지 초과 시 조회 기간을 절반씩
   재귀 분할(깊이 5). 30일 창은 API 상한을 넘는다.
3. **오류 봉투 함정** — 나라장터는 오류를 `{"nkoneps.com.response.ResponseError":{...}}`
   라는 **다른 루트 키**로 준다. `data.response` 만 읽으면 오류가 "데이터 없음"으로 보인다.
   측정치로 240건 중 89건이 이렇게 새고 있었다.
4. **캐시** — 검색 2시간/500건, G2B 호출 6시간/2000건, 진행 중 요청 합치기(coalescing).
   첨부 캐시는 DB 기반이며 그대로 유지한다.
5. **업로드 파일명 latin1→UTF-8 복원** — 한글 파일명이 깨진다.
6. **SSRF 허용목록** — 원본에 두 벌이 미묘하게 다르게 존재한다. 하나로 합치고
   **리다이렉트 홉마다** 재검증한다.
7. **AI 실패 시 5xx 금지** — §1.1 참고.
8. **`SPEC_MAX_CHARS = 50000`** 과 `clampToContext()` 산술(문자/토큰 1.8, 프롬프트 여유
   1500 토큰) — 상수만 옮기지 말고 계산식을 옮길 것.
9. **내보내기 취소 시 25행마다 취소 확인** — 작업 등록이 취소된 작업을 되살리기 때문에,
   이 확인이 없으면 취소가 무력화된다(과거 수정된 버그).
10. **`llmModel(deep)`** 은 호출부 6곳에서 인자를 넘기지만 선언은 무인자다
    (`server.js:210`) — 심층/빠른 구분이 지금은 조용히 무시되고 있다. 이식 시 의식적으로 결정할 것.
