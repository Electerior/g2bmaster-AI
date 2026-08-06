# 결정 기록 — g2bmaster-AI

`CLAUDE.md §8`: 원칙을 어겨야 하면 어기되, **무엇을 왜 어겼는지 남긴다.**
이유 없는 규칙은 다음 사람이 정리해 버린다.

`Principles §7.3`: "이건 백엔드와 정해야 한다" 가 나오면 구현으로 우회하지 말고
여기 적고 역제안한다. §2 가 그 목록이다.

---

## 1. 결정

### D-001 — 스택: TypeScript(ESM) + Fastify + vitest, Node ≥ 22

**상태:** ⚠️ **D-009 가 뒤집을 가능성이 높다** · 2026-08-06

> 이 결정은 **원격 저장소를 보지 못한 상태에서** 내려졌다. `origin/main` 에 이미
> Python 구현이 있고 더 앞서 있다. 아래 D-009 를 먼저 읽어라.

`CLAUDE.md §6 전제 A` 는 "저장소에 기존 Node 모듈이 이미 들어 있다" 를 가정했다.
**틀렸다.** 이 저장소에는 문서와 TypeScript 초안 8개뿐이었고, 커밋조차 없었다.
원본 모놀리스는 별도 저장소 `../g2bmastersopen` 에 있다(`lib/lms.js`, `lib/law-mcp.js`,
`price-web.js`, `module_a/`, `module_b/`, `korean-law-mcp/` 전부 거기 있다).

→ "빈 저장소면 스택 선택부터 다시" 라는 조건이 발동했고, 다음을 택했다:

- **TypeScript ESM** — 초안 코드가 이미 그렇게 쓰여 있었고, `degrade`/`fatal` 구분을
  타입으로 강제하는 것(`§2-2`)이 이 저장소의 핵심 안전장치다. 타입 없이는 집행이 안 된다.
- **Fastify** — JSON Schema 가 검증과 직렬화를 동시에 한다. 스키마에 없는 필드는
  응답에서 사라지므로 "코드에는 있는데 백엔드엔 안 가는 필드" 가 구조적으로 불가능해진다.
  (원본은 Express 지만, 여기서는 계약 집행이 더 중요하다.)
- **vitest** — 원본 저장소가 이미 vitest 를 쓴다. 사람이 옮겨 다닐 때 마찰이 없다.
- **strict + `exactOptionalPropertyTypes` + `noUncheckedIndexedAccess`** — 계약 필드의
  "없음" 과 "undefined" 는 JSON 직렬화에서 다르게 나온다. 그 차이가 백엔드 파싱을 깬다.

### D-002 — `NOT_IMPLEMENTED` = 503 + `retryable: false`

**상태:** 백엔드 합의 필요 · 2026-08-06

M0 의 8개 미구현 표면이 내는 실패다. 관례(5xx = 재시도 가능)를 의도적으로 벗어난다.

- 503 → 백엔드의 `AiUnavailableException` 경로를 타야 200 폴백이 만들어지고,
  그 폴백 계약을 검증하는 것이 M0 의 존재 이유다. 501 은 그 경로를 안 탄다.
- `retryable: false` → 지금 다시 불러도 결과가 같다. 재시도 예산을 태우면 큐가 굶는다.

**백엔드에 확인할 것:** `AiClient` 가 status 로 분기하는가, `code`/`retryable` 로
분기하는가. 후자여야 이 결정이 성립한다. 전자라면 표를 다시 짜야 한다.

### D-003 — `item-summary` 파이프라인은 M3 까지 등록하지 않는다

**상태:** 확정 · 2026-08-06

`src/pipeline/item-summary/` 와 `src/http/routes/itemSummary.ts` 는 트리에 있지만
라우트로 등록하지 않는다. 호출할 `ChatClient` 가 아직 없기 때문이다.
`/api/item-summary` 는 M0 에서 다른 7개와 같이 503 을 낸다.

커버리지 게이트에서도 제외했다(`vitest.config.ts`). 아직 계약이 확정되지 않은 표면에
테스트를 먼저 박으면 M3 에서 그 테스트를 다시 뜯게 된다.

**M3 의 DoD:** 이 두 경로를 커버리지 제외에서 빼고, `PENDING_ENDPOINTS` 에서
`/api/item-summary` 를 지우고 `registerRoutes` 에 붙인다.

### D-004 — 프롬프트 버전이 `Function.prototype.toString()` 에 의존한다 (위험)

**상태:** M2 전에 해소할 것 · 2026-08-06

`promptRegistry.derive()` 는 `def.render.toString()` 을 해시 재료로 쓴다.
템플릿 변경을 자동으로 잡아내는 영리한 방법이지만, **함수 소스 텍스트는 의미가 아니라
빌드 산출물**이다. tsc 와 esbuild 는 같은 함수를 다르게 출력하고, 압축을 켜면 또 달라진다.

즉 **코드를 한 글자도 안 고쳤는데 번들러 설정만 바꿔도 프롬프트 버전이 바뀐다.**
그러면 백엔드의 분석 재사용 캐시가 통째로 고아가 되고 전부 재추론한다 —
`ai-boundary.md §6.2` 가 경고한 `analysisInputHash` 고아 문제와 정확히 같은 종류다.

**해소 방향:** 프롬프트 템플릿을 함수가 아니라 **문자열 자산**으로 옮기고(예: `.md`/`.txt`
파일 또는 템플릿 문자열 상수), 그 문자열을 해시 재료로 쓴다. 렌더러는 순수 함수로
따로 두되 해시에는 넣지 않고, 렌더링 규칙 변경은 `postprocessRev` 로 올린다.

그때까지 `test/golden/promptRegistry.test.ts` 는 해시 값을 박아 두지 않고
**차분 검증**만 한다 — 하드코딩하면 빌드 도구가 바뀔 때 거짓 실패를 낸다.

### D-005 — 빈 인용은 기각한다

**상태:** 확정 · 2026-08-06

`verifyFacts` 의 축자 대조는 빈 문자열 `''` 를 **어떤 원문에서도 발견**한다.
그래서 `evidence.quote` 가 비어 있으면 검증을 그냥 통과했고, 백엔드의 원문 대조도
똑같이 통과시킨다 — 근거 없는 사실이 채택된다.

`Principles §2.4` ("검증받을 수 있는 형태로 반환한다")를 조용히 무력화하는 구멍이라
명시적으로 기각하도록 고쳤다. `test/golden/citation.test.ts` 가 고정한다.

### D-006 — 호출자 쪽 오류(4xx)는 `BAD_REQUEST` 로 분류한다

**상태:** 확정 · 2026-08-06

Fastify 의 본문 파서가 던지는 오류(깨진 JSON, 본문 상한 초과, 지원하지 않는
content-type)에는 `validation` 필드가 없고 `statusCode` 만 4xx 다.
판별을 `validation` 하나에만 걸면 이 경로가 `INTERNAL`(500, **재시도 가능**)으로
새어 나가고, **절대 성공할 수 없는 요청을 백엔드가 재시도 예산이 다할 때까지 반복한다.**

→ `statusCode` 4xx 도 함께 본다(`src/app.ts` `isCallerError`).

### D-007 — 운영용 `/health` 를 따로 두지 않는다

**상태:** 확정 · 2026-08-06

`GET /api/ai/capacity` 가 이미 계약된 표면이고, 프로세스가 살아 있는지와 실효 용량이
얼마인지를 함께 답한다. 별도 `/health` 를 두면 우리가 소유하는 표면이 11개가 아니게 되고,
"살아 있음" 과 "일할 수 있음" 이 갈려 오케스트레이터가 잘못된 쪽을 본다.

### D-008 — `legacy/` 는 아직 존재하지 않는다

**상태:** M1 에서 해소 · 2026-08-06

`CLAUDE.md §2-3` 은 "`legacy/` 는 감싸기만 한다" 를 말하지만 M0 시점에 그 디렉터리는
없다. 원본 모듈은 `../g2bmastersopen/lib/` 에 있고, **아직 이 저장소로 가져오지 않았다.**

M1(embed)에서 처음 필요해진다. 그때 결정할 것: 원본 파일을 복사할 것인가,
서브모듈/패키지로 참조할 것인가. 어느 쪽이든 **원본을 리팩터링하지 않는다** —
쿨다운·페일오버 같은 과거 장애의 흔적이 사라진다.

**주의:** `ai-boundary.md §2` 는 `lib/llm-worker-pool.js` 를 목록에 올렸지만
원본 저장소의 `lib/` 에 그 파일은 **없다.** 다중 엔드포인트 부하 분산이 실제로 어디에
있는지(아마 `lib/lms.js` 안) M1 전에 확인해야 한다.

### D-009 — 같은 서비스의 구현이 두 개다 (Python vs TypeScript) — **미해결**

**상태:** 🔴 **결정 대기** · 2026-08-06

`origin/main` 을 처음 가져와 보니 **이미 Python 구현이 있었고, 이 저장소의 정식 구현이다.**
로컬 TypeScript 작업(D-001)은 그것을 모른 채 병렬로 만들어진 것이다.

| | Python (`origin/main`) | TypeScript (로컬 `main`) |
|---|---|---|
| 진척 | `prompt-version`·`capacity`·`models`·**`embed`** 완료 (≈M1) | `prompt-version`·`capacity`·`models` (M0) |
| LLM 연동 | **있다.** LM Studio 4종 모델로 실측, 워커 풀 쿨다운·페일오버 | 없다. `ChatClient` 미배선 |
| 모듈 서버 | `module_a/`·`module_b/`·`korean-law-mcp/` 포함 | 없음 |
| 미구현 응답 | `501 NOT_PORTED` | `503 NOT_IMPLEMENTED` (D-002) |
| 포트 | 8000 (백엔드 `AI_BASE_URL` 기본값) | 8100 |
| 테스트 | `scripts/test_http_contract.py` 등 | vitest 106개, 커버리지 99.5% |

**공통 조상이 없다.** `dev` 브랜치에서 `--allow-unrelated-histories` 로 병합했고,
코드 트리가 서로 겹치지 않아 충돌은 `.gitignore`·`.env.example` 둘뿐이었다.
즉 지금 트리에는 **같은 11개 표면을 각각 들고 있는 서버가 둘** 있다.

**권고: Python 을 남긴다.** 이유는 취향이 아니다.

1. 더 앞서 있고, **실제 모델에 붙는 것이 확인된** 유일한 구현이다.
2. `module_a`·`module_b`·`korean-law-mcp` 가 이미 Python 이다(`ai-boundary.md §2`).
   TS 를 고르면 이 프로세스들과 영원히 언어가 갈린다.
3. README 가 이미 백엔드·프론트에 그 스택을 공표했다.

**TS 쪽에서 건질 것** (버리기 아까운 것만):

- `docs/failure-modes.md` — 실패 분류표. 언어와 무관하다.
- 인용 검증의 빈 인용 기각(D-005)과 4xx 분류(D-006) — Python 쪽에 같은 구멍이 있는지 확인할 것.
- 프롬프트 버전이 함수 소스에 의존하는 위험(D-004) — Python 이식본도 같은 방식인지 확인할 것.
- `test/fault/*` 가 고정한 계약(재시도 한 겹, 데드라인 부등식) — 테스트 자체는 못 옮기지만
  **무엇을 검증해야 하는지의 목록**으로는 그대로 쓸 수 있다.

**정하기 전까지 두 구현을 함께 두는 비용:** 계약을 고칠 때마다 두 번 고쳐야 하고,
한쪽만 고치면 조용히 갈린다. 오래 끌 결정이 아니다.

---

## 2. 백엔드와 합의가 필요한 목록

`Principles §7.3` 의 목록에 M0 에서 나온 항목을 더한 것이다.
`ai-boundary.md §6` 은 현재 네 항목뿐인데, 최소한 다음이 더 필요하다:

| # | 항목 | 출처 |
|---|---|---|
| 1 | `embeddingVersion` 의 저장 여부 | Principles §3.4 |
| 2 | 엔드포인트별 타임아웃과 리스 시간의 부등식 | Principles §4.2 |
| 3 | 인용 좌표계 — 문자열 대조인가 offset 대조인가 | Principles §2.4 |
| 4 | `_analysisHistoryId` 의 생성 주체 (이력을 소유한 쪽이 맞다) | Principles §7.3 |
| 5 | `prompt-version` 을 엔드포인트별 맵으로 확장할지 | CLAUDE.md 전제 F |
| 6 | **`AiClient` 가 status 로 분기하는가 code/retryable 로 분기하는가** | D-002 |
| 7 | **미구현 표면의 실패를 백엔드가 재시도 대상으로 볼 것인가** | D-002 |
| 8 | **부분 결과 + 데드라인 초과 시 `200 degraded` 가 맞는가** | CLAUDE.md 전제 D |

M0 은 5·6·7·8 을 실제로 **호출해 볼 수 있는 형태**로 열어 뒀다.
백엔드가 `g2b.ai.enabled=true` 로 M0 을 가리키면 폴백 경로 전체를 LLM 없이 검증할 수 있다.

---

## 3. 아직 미확인인 전제

`CLAUDE.md §6` 의 표 중 M0 에서 해소되지 않은 것:

| # | 전제 | 왜 아직 미확인인가 |
|---|---|---|
| B | `item-summary` 4단계 · 첨부 파싱은 백엔드가 끝내고 `documents[].text` 로 넘겨줌 | M3 까지 표면이 안 열려 검증 기회가 없다 |
| C | `documentSignals` 분담 (우리는 `legalAssessment`·`summary` 만) | 위와 같음 |
| D | 부분 결과 + 데드라인 초과 시 `504` 보다 `200 degraded` 가 낫다 | 커널은 그렇게 구현했으나 백엔드 재시도 정책과 아직 안 맞춰 봤다 |
| E | `_analysisHistoryId` 는 백엔드가 만든다 (우리는 넣지 않는다) | 응답 스키마에 넣지 않는 것으로 구현했다. 합의는 아직 |
| F | `prompt-version` 을 엔드포인트별 맵으로 확장 | 단일 값과 맵을 **둘 다** 내보내는 것으로 우회했다 |

A 와 G 는 해소됐다 — D-001 과 `CLAUDE.md §7`.
