# AI 경계 — g2bmaster-backend ↔ g2bmaster-AI

이 문서는 **저장소를 셋으로 나눌 때 가장 중요한 결정**을 기록한다:
무엇이 Java 로 넘어오고, 무엇이 AI 저장소에 남는가.

> 현 단계에서 `g2bmaster-AI` 저장소는 **손대지 않는다**. 이 문서는 백엔드가 기대하는
> 계약을 적어 둔 것이고, AI 쪽 구현은 이후 작업이다. 그때까지 `g2b.ai.enabled=false`
> 로 두면 백엔드는 AI 없는 기능만으로 정상 동작한다.

---

## 1. 원칙

**백엔드는 추론하지 않는다.** 모델을 호출하거나, 프롬프트를 만들거나, 임베딩을
생성하거나, 법령 MCP 와 대화하지 않는다. 백엔드가 소유하는 것은 그 주변의
**내구성 있는 상태**다 — 작업 큐, 리스, 재시도, 중복 제거, 결과 이력, 내보내기.

원본 모놀리스에도 이미 이 이음매가 있었다. `lib/analysis-executor.js` 는 23줄짜리
HTTP 클라이언트로, 자기 서버의 `/api/item-summary` 를 호출하고 응답을 검증했다.
저장소를 나누면서 **호출 대상만 AI 서비스로 바뀌었을 뿐 구조는 그대로**다.

---

## 2. AI 저장소에 남는 것 (Java 로 이식하지 않음)

| 원본 모듈 | 역할 |
|---|---|
| `lib/lms.js` | LM Studio / OpenAI 호환 채팅 클라이언트, 모델·컨텍스트 창 탐지 |
| `lib/llm-worker-pool.js` | 다중 엔드포인트 LLM 부하 분산, 쿨다운·페일오버 |
| `lib/law-mcp.js` | `korean-law-mcp` 로의 MCP JSON-RPC (`review_illegality`) |
| `lib/legal-review.js` | 법령 검색·조항 검토·콜드메일 초안 |
| `lib/pledge-workflow.js` | LLM 기반 서약서 수정본 생성·검증 |
| `lib/pledge-revision.js` | 위 둘의 오케스트레이션 |
| `lib/ai-config.js` | LLM 튜닝 파라미터·프롬프트·공급자 비밀값 |
| `price-web.js` | studyweb + LLM 가격 추출 |
| `module_a/`, `module_b/`, `korean-law-mcp/` | Python 모듈 서버 일체 |

`lib/ai-config.js` 중 백엔드에 남는 것은 **접속 정보뿐**이다
(`g2b.ai.base-url`, `g2b.ai.timeout-ms`, `g2b.ai.enabled`).
`llmTemperature`, `llmTopP`, `pricePrompt` 같은 값은 전부 AI 저장소 소관이다.

## 3. 백엔드로 오는 것 (오케스트레이션 — 추론 없음)

| 원본 모듈 | Java |
|---|---|
| `lib/analysis-job-queue.js` | 작업 큐 (`FOR UPDATE SKIP LOCKED`, 리스, 재시도, 중복 제거) |
| `lib/analysis-job-runner.js` | 워커 루프 (claim → heartbeat → execute → complete/fail) |
| `lib/analysis-history.js` | 내용 주소화 결과 캐시 |
| `lib/search-analysis.js` | 검색 결과 → 분석 작업 일괄 등록 |
| `lib/export-job-service.js` | 내보내기 작업 상태 기계 |
| `lib/analysis-executor.js` | → `integration/ai/AiClient` (계약 그대로) |

## 4. 반씩 갈리는 것

| 원본 모듈 | AI 저장소에 남음 | 백엔드로 이식 |
|---|---|---|
| `lib/procurement-analysis.js` | `analyzeProcurementMarkdown` | `normalizeEvidence` + **근거 인용 검증 규칙** |
| `lib/local-price-db.js` | `inferSystemFromParts`, `formatSystemsForLlm` | 엑셀 카탈로그 적재, `findBestMatch`, `findMatchingSystems` |
| `lib/market-price-resolver.js` | `resolveMarketPrice`(외부 견적 수집) | `deriveProductIdentity`, `quoteMatchesIdentity`, 정규화기 |
| `lib/prebuilt-comparables.js` | 주입되는 검색 fetcher | `classifyPrebuiltBundle`, `rankPrebuilts`, `mergePrebuiltResults` |
| `lib/attachment-ingest.js` | `embed(texts)` | 적재 루프, `rankByEmbedding`, `cosine`, 모든 DB 쓰기 |
| `alerter.js` | `generateSalesEmail` | 조회·점수화·렌더링·발송·Slack |

> **`procurement-analysis.js` 의 근거 검증은 AI 기능이 아니라 정확성 보증이다.**
> LLM 은 사실을 *제안*할 뿐이고, 채택 여부는 `evidence.quote` 가 원문에 문자 그대로
> 존재하는지로 결정된다. 이 규칙은 반드시 백엔드에 두어야 한다 — AI 응답을 검증하는
> 쪽이 AI 자신이면 검증이 아니다.

---

## 5. 백엔드가 호출하는 AI 엔드포인트

`integration/ai/AiClient` 가 부르는 것들. 기본 주소는 `g2b.ai.base-url`.

| 메서드 | 경로 | 용도 |
|---|---|---|
| POST | `/api/item-summary` | 공고·사전규격·발주계획 심층 분석 (작업 큐가 소비) |
| POST | `/api/bid-summary` | 공고 영업 요약 |
| POST | `/api/legal/review-clauses` | 조항 위법성 검토 |
| POST | `/api/legal/outreach-draft` | 콜드메일 초안 |
| POST | `/api/pledge/revision-workflow` | 서약서 수정본 생성 |
| POST | `/api/price/resolve` | 품목명 → 웹 가격 |
| POST | `/api/price/url` | URL 지정 가격 조회 |
| POST | `/api/embed` | 텍스트 임베딩 (유사도 계산은 백엔드가 함) |
| GET | `/api/ai/prompt-version` | 분석 재사용 키에 들어가는 프롬프트 버전 |
| GET | `/api/ai/capacity` | 워커 용량 (내보내기 ETA 계산용) |
| GET | `/api/llm/models` | 모델 목록·도달 여부 (시스템 화면) |

---

## 6. 두 저장소가 반드시 합의해야 하는 네 가지

### 6.1 프롬프트 버전

현재 값은 `'item-summary-2026-08-04-v4'`. 이것은 분석 결과 재사용 키의 일부다.
**백엔드가 하드코딩하면 안 된다** — AI 쪽이 프롬프트를 고쳤는데 백엔드가 모르면
낡은 결과를 계속 재사용한다. `GET /api/ai/prompt-version` 으로 읽어온다.

### 6.2 `analysisInputHash` 정규화 — 가장 위험한 항목

입력 payload 를 정규화(키 정렬, `__rowId`·`_cached`·`_opportunityScore` 같은
휘발성 필드 제거)한 뒤 SHA 를 뜬 값이다. **Node 와 Java 에서 바이트 단위로 같아야 한다.**
어긋나면 이관 순간 기존 분석 캐시가 통째로 고아가 되고, 전부 다시 추론하게 된다.

→ 다른 무엇보다 **먼저 교차 언어 고정값(fixture) 테스트를 만들 것.**
원본 구현은 `lib/analysis-history.js` 의 `analysisInputHash`.

### 6.3 응답 필드

- `_analysisHistoryId` — 없으면 작업을 완료로 기록하지 않는다.
- `aiDisabled` / `aiFallback` — **성공으로 치지 않는다.** 폴백 결과가 캐시에 눌러앉으면
  영원히 재분석되지 않는다.
- `aiError` — 사용자에게 보여줄 한국어 사유.

### 6.4 HTTP 200 폴백 계약

AI 분석 엔드포인트는 **LLM 이 실패해도 프론트에는 200 을 준다**. 첨부 문서에서 뽑아낸
문서 태그·법령 검토·규격 원문은 LLM 과 무관하게 유효하고, 사용자는 그것만으로도
판단을 이어갈 수 있기 때문이다. `GlobalExceptionHandler` 가 이 계약을 삼키지 않도록
AI 컨트롤러는 `AiUnavailableException` 을 직접 잡아 폴백 응답으로 바꾼다.

---

## 7. AI 없이 도는 범위

`g2b.ai.enabled=false` 일 때 정상 동작하는 것:

- 입찰공고·사전규격·발주계획·개찰결과 검색 전부
- 트렌드 집계 3종
- 낙찰자 이력·담당자 조회·들러리 매트릭스
- 저장 공고 CRUD
- 시스템 현황·적재·스케줄
- 첨부 다운로드 프록시, 첨부 텍스트 추출, 문서 태그·독소조항 규칙 판정
- 수주기회 점수(`lib/scoring.js` 는 순수 규칙 기반이다)

동작하지 않는 것: 심층 AI 분석, 웹 가격 조회, 법령 위법성 판단, 서약서 수정본,
임베딩 기반 유사도 재정렬, 그리고 이들에 의존하는 엑셀 내보내기의 AI 열.
