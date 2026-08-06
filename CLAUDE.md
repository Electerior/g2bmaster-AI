# CLAUDE.md — g2bmaster-AI

이 저장소는 **추론 전담 서비스**다. 프론트와 직접 통신하지 않는다.
호출자는 백엔드(Java, `g2bmaster-backend`)의 `integration/ai/AiClient` 하나뿐이다.

작업 시작 전에 이 파일의 **§6 확인되지 않은 전제**를 먼저 읽어라.
거기 적힌 항목이 미확인 상태라면 그 영역의 코드를 쓰기 전에 사용자에게 확인한다.

---

## 1. 문서 권위 순서

1. `../g2bmaster-backend/docs/ai-boundary.md` — **경계 계약. 최상위 권위.**
2. `../g2bmaster-backend/docs/api-contract.md` — 프론트↔백엔드 계약.
   **우리가 지키는 문서가 아니다.** 배경 지식으로만 읽는다.
3. `Principles.md` — 판단 기준
4. `docs/failure-modes.md` — 실패 분류표 (없으면 만들 것)
5. `docs/decisions.md` — 결정 기록과 미합의 목록 (없으면 만들 것)
6. 이 파일

충돌하면 위쪽이 이긴다. 단 §6의 미확인 항목은 어느 것도 이기지 못한다 — 검증이 먼저다.

`Principles.md §5.1`은 `item-summary`를 6단계로 적고 있는데 이는 **모놀리스 기준이라 낡았다**.
§3을 따른다.

---

## 2. 절대 규칙

어기면 두 저장소가 동시에 깨지거나, 더 나쁘게는 조용히 잘못 동작한다.

1. **실패를 200으로 포장하지 않는다.** HTTP 200 폴백은 백엔드 컨트롤러가
   `AiUnavailableException`을 잡아 만드는 것이다. 우리가 미리 포장하면 백엔드가
   실패를 구분하지 못하고, 폴백 결과가 캐시에 눌러앉아 영원히 재분석되지 않는다.
2. **`degrade`와 `fatal`을 타입으로 구분한다.** 스텝은 예외를 던지지 않고
   `{ok} | {degrade} | {fatal}`을 반환한다. 예외를 던지면 "계속 갈 수 있음"과
   "여기서 끝"의 구분이 사라지고 전부 500이 된다.
3. **`legacy/`는 감싸기만 한다.** 원본 Node 모듈을 리팩터링하지 않는다.
   과거 장애로 굳어진 코드(쿨다운·페일오버 등)의 흔적이 사라진다.
   계약 준수는 전부 감싸는 층의 책임이다.
4. **프롬프트 버전은 자동 파생한다.** `프롬프트 해시 + 모델 ID + 디코딩 파라미터 해시`.
   손으로 문자열을 고치는 방식은 반드시 잊어버리고, 그러면 백엔드가 낡은 결과를 재사용한다.
5. **인용은 `{documentId, quote, offset, length}`로 반환하고 반환 직전 자체 검증한다.**
   최종 채택 여부는 백엔드가 원문 대조로 판정한다. 우리는 그 검증을 쉽게 만들 뿐이다.
6. **원본 텍스트 좌표계를 훼손하지 않는다.** 프롬프트용 정규화를 하면 LLM은 정규화본을
   인용하고 백엔드는 원본과 대조해 **모든 인용이 기각된다.** 되돌릴 수 없는 변형 금지.
7. **`AI 자체 데드라인 < g2b.ai.timeout-ms < 백엔드 리스 시간`** 을 항상 성립시킨다.
   넘기면 다른 워커가 같은 작업을 다시 집어 LLM 비용이 두 배로 나간다.
8. **재시도는 한 겹만.** 백엔드에 이미 재시도가 있다. 우리 안에서는 같은 요청 내에서
   값싸게 회복 가능한 것(JSON 파싱 실패, 엔드포인트 페일오버)만 재시도한다.
9. **한국어 오류 문구는 상수로 모은다.** `aiError`로 화면에 그대로 렌더링된다.
   코드에서 조립하지 않는다. 내부 원인은 `code`로 따로 보낸다.
10. **내구 상태를 갖지 않는다.** 큐·이력·벡터 저장은 백엔드 소유.
    프로세스가 죽어서 잃는 것이 진행 중이던 요청 하나뿐이어야 한다.

---

## 3. 우리가 소유하는 표면

`ai-boundary.md` §5의 11개.

| 메서드 | 경로 | 비고 |
|---|---|---|
| POST | `/api/item-summary` | 4스텝: clamp → facts → summary → items (+legal) |
| POST | `/api/bid-summary` | 영업 요약. item-summary와 프롬프트가 다름 |
| POST | `/api/legal/review-clauses` | law MCP |
| POST | `/api/legal/outreach-draft` | 콜드메일 초안 |
| POST | `/api/pledge/revision-workflow` | 태그 없으면 첨부 접근 전 `400 TAG_MISSING` |
| POST | `/api/price/resolve` | `queryRelaxed` 필수 보고 |
| POST | `/api/price/url` | |
| POST | `/api/embed` | `{model, embeddingVersion, dim, vectors[]}` |
| GET | `/api/ai/prompt-version` | 자동 파생값 |
| GET | `/api/ai/capacity` | 백엔드 ETA 계산용 |
| GET | `/api/llm/models` | 시스템 화면용 |

별도 프로세스: `module_a/`, `module_b/`, `korean-law-mcp` (Python). 백엔드가 직접 프록시한다.

---

## 4. 우리가 소유하지 않는 것

착각하기 쉬운 것들만 적는다. 여기 있는 걸 구현하려 들면 잘못 가고 있는 것이다.

- 작업 큐·리스·재시도 장부·결과 이력·재사용 캐시 조회
- 첨부 다운로드(SSRF 가드 포함)와 텍스트 추출(HWP/HWPX 등)
- 문서 태그·독소조항 규칙 판정 — 규칙 기반이라 `ai.enabled=false`에서도 동작
- 수주기회 점수 `_opportunity*` — `lib/scoring.js`는 순수 규칙
- 임베딩 유사도 계산과 벡터 저장 (우리는 벡터만 만든다)
- `evidence.quote` 최종 채택 판정
- 가격 `result = null` 강제 (우리는 `queryRelaxed`만 보고)
- `POST /api/analysis-jobs/status` — 큐가 백엔드 소유이므로 우리에게 없다
- `deriveProductIdentity`, `quoteMatchesIdentity`, `rankPrebuilts` 등 순수 판정 함수

---

## 5. 구조

**스택은 Python (FastAPI)이다.** `docs/decisions.md` D-A.

```
app/
  main.py         HTTP 표면 11개. 라우트 = 얇은 어댑터, 로직 없음
  config.py       설정. data/ai-config.json > 환경변수 > 기본값 (원본과 같은 우선순위)
  prompts.py      프롬프트 버전. 재사용 키의 일부다
  embedding.py    POST /api/embed
  llm/client.py   LM Studio·OpenAI 호환 클라이언트. 원본 lib/lms.js
  llm/worker_pool.py  다중 엔드포인트 분배·쿨다운·페일오버. 원본 lib/llm-worker-pool.js
server.py         uvicorn 진입점
module_server.py  원본 module_a/module_b 서버
module_a/ module_b/ korean-law-mcp/   Python 모듈 서버. 백엔드가 직접 프록시한다
scripts/          test_http_contract.py · test_worker_pool.py · smoke_llm.py
```

진척은 `PORTING_STATUS.md` 가 유일한 출처다. "곧 됩니다"는 쓰지 않는다.
현재: `prompt-version`·`capacity`·`models`·`embed` 완료, 나머지 7개는 `501 NOT_PORTED`.

남은 순서: **bid-summary** → **item-summary**(백엔드 첨부 파싱 대기) →
**price** → **legal·pledge**.

각 표면의 DoD: `scripts/test_http_contract.py` 통과 + `PORTING_STATUS.md` 갱신 +
실패 응답이 `docs/failure-modes.md` 의 모양을 지킬 것.

---

## 6. 확인되지 않은 전제

**아래는 백엔드 문서 두 개에서 추론한 것이지 확인된 사실이 아니다.**
해당 영역 작업 전에 사용자에게 확인하고, 확인되면 이 절에서 지우고 본문에 반영한다.

| # | 전제 | 확인되면 영향 |
|---|---|---|
| ~~A~~ | ~~저장소에 기존 Node 모듈이 이미 들어 있다~~ | **해소(틀림).** 이 저장소는 비어 있었다. 원본은 `../g2bmastersopen/lib/` 에 있고 아직 가져오지 않았다. 스택은 `docs/decisions.md` D-001 에서 다시 정했다 |
| B | `item-summary`는 4단계다. 재사용 캐시·첨부 파싱은 백엔드가 끝내고 `documents[].text`로 넘겨준다 | §3, §4, 파이프라인 전체 |
| C | `documentSignals`는 갈린다. `documentTags`·`bidBlockingClauses`는 백엔드, 우리는 `legalAssessment`·`summary`만 | 응답 조립 주체 |
| D | 부분 결과 + 데드라인 초과 시 `504`보다 `200 degraded`가 낫다 | 백엔드 워커 재시도 정책과 맞물림 |
| E | `_analysisHistoryId`는 이력을 소유한 백엔드가 만든다 (우리는 넣지 않는다) | 응답 스키마 |
| F | `GET /api/ai/prompt-version`을 엔드포인트별 맵으로 확장한다 | `bid-summary`는 프롬프트가 다름. **단일 값과 맵을 둘 다 내보내는 것으로 우회 중** |
| ~~G~~ | ~~빌드·테스트·실행 명령 일체~~ | **해소.** 아래 §7 |

B~F 는 여전히 미확인이다. 해당 영역 코드를 쓰기 전에 확인한다.
M0 은 이 중 어느 것에도 의존하지 않기 때문에 먼저 낼 수 있었다.
합의가 필요한 전체 목록은 `docs/decisions.md §2`.

---

## 7. 명령어

```
설치:        make install       # 임베딩까지 쓰려면 make install-ml
설정:        cp .env.example .env
실행:        make start         # http://127.0.0.1:8000
계약 테스트:  python scripts/test_http_contract.py
워커 풀:     python scripts/test_worker_pool.py
LLM 연기:    python scripts/smoke_llm.py    # 실제 모델에 붙는다
```

백엔드가 요구하는 것은 하나뿐이다 — **`AI_BASE_URL`(기본 `http://localhost:8000`)에서
HTTP 로 응답할 것.** 언어도 프레임워크도 백엔드는 모른다.

```
cd ../g2bmaster-backend && AI_ENABLED=true AI_BASE_URL=http://localhost:8000 ./mvnw spring-boot:run
```

### 지금 손대야 할 것

`docs/decisions.md §1` 에 코드를 읽고 확인한 항목이 있다. 시급한 순서로:

1. **F-4** — `501 NOT_PORTED` 가 백엔드의 `AiUnavailableException` 경로를 타는지 확인.
   안 타면 프론트가 날것의 501 을 받고 AI 없이도 되는 화면까지 같이 죽는다.
2. **F-1** — 실패 본문 모양이 다섯 가지다. `code`·`retryable`·`requestId` 가 빠져 있다.
   표준형은 `docs/failure-modes.md`.
3. **F-3** — 단일 `promptVersion` 은 `bid-summary` 가 들어오는 순간 깨진다. M2 이전에 정한다.

---

## 8. 작업 방식

- 계약 변경이 필요해지면 **구현으로 우회하지 말고** `docs/decisions.md`에 적고
  백엔드에 역제안한다. `Principles.md §7.3`에 현재 목록이 있다.
- 원칙을 어겨야 하면 어기되, **무엇을 왜 어겼는지 주석과 결정 기록에 남긴다.**
  이유 없는 규칙은 다음 사람이 정리해 버린다.
- 새 필드 추가는 자유롭게, 제거·이름 변경·의미 변경은 백엔드 합의 후에.
- 추측으로 코드를 채우지 말고 §6을 먼저 해소한다.
