# 결정 기록 — g2bmaster-AI

`CLAUDE.md §8`: 원칙을 어겨야 하면 어기되, **무엇을 왜 어겼는지 남긴다.**
`Principles §7.3`: "이건 백엔드와 정해야 한다" 가 나오면 구현으로 우회하지 말고 여기 적는다.

---

## 0. 이 문서의 내력

2026-08-06, 원격 저장소를 확인하지 않은 채 TypeScript 로 같은 서비스를 다시 만든 일이
있었다. `origin/main` 에는 이미 Python 구현이 있었고 더 앞서 있었다
(`embed` 까지 완료, LM Studio 실측 확인).

**원인:** 로컬 브랜치에 커밋이 없어서 `git log` 가 "does not have any commits yet" 을
출력했고, 그것을 "빈 프로젝트" 로 읽었다. 원격에는 커밋이 두 개 있었다.
`git remote -v` 한 번이면 끝날 일이었다.

**교훈:** 로컬 커밋이 0 개인 저장소에서는 **원격을 먼저 본다.**
`CLAUDE.md §6 전제 A`("저장소에 기존 모듈이 있다")는 사실 **맞았다** — 이 작업 디렉터리가
아니라 원격에 있었을 뿐이다.

TypeScript 구현은 `dev` 에서 제거했다. 커밋 `95eeec0` 에 온전히 남아 있으므로
필요하면 `git checkout 95eeec0 -- src test` 로 되살릴 수 있다.
아래 §1 은 그 작업에서 나온 것 중 **Python 구현에 실제로 해당하는 것만** 옮긴 것이다.

---

## 1. Python 구현에서 확인한 것

TypeScript 쪽에서 계약을 파고들며 나온 항목을 `app/` 에 대조해 봤다.
추측이 아니라 코드를 읽고 확인한 결과다.

### F-1 — 실패 응답의 모양이 다섯 가지다 🔴 **가장 시급**

`app/main.py` 가 현재 내보내는 실패 본문:

| 경로 | status | 본문 |
|---|---|---|
| `not_ported()` | 501 | `{code, error, reason, blockedBy?}` |
| `embed` 실패 | 503 | `{code, error}` |
| `not_found` 핸들러 | 404 | `{error, path}` — **`code` 없음** |
| `service_secret_guard` | 401 | `{error}` — **`code` 없음** |
| FastAPI 본문 검증 실패 | 422 | `{detail: [...]}` — **`code`·`error` 둘 다 없음** |

→ **백엔드가 모든 실패를 하나의 파서로 읽을 수 없다.** 어느 갈래로 들어오느냐에 따라
본문 모양이 달라지므로, `AiClient` 는 경로마다 다른 파싱을 해야 한다.

두 가지가 **어디에도 없다**:

- **`retryable`** — 백엔드가 재시도 여부를 판단할 근거. 지금은 status 로 추측해야 하는데,
  4xx/5xx 관례만으로는 "지금 다시 부르면 될까" 를 알 수 없다.
- **`requestId`** — 백엔드 로그의 작업 하나와 우리 로그의 요청 하나를 잇는 유일한 실.
  없으면 장애 조사 때 두 저장소의 로그를 대조할 방법이 없다.

→ 제안하는 단일 표는 `docs/failure-modes.md` 에 있다. 언어와 무관하게 그대로 쓸 수 있다.
`422` 는 FastAPI 기본 핸들러를 덮어써야 잡힌다(`@app.exception_handler(RequestValidationError)`).

### F-2 — `promptVersion` 이 손으로 고치는 상수다

```python
ITEM_SUMMARY_PROMPT_VERSION = "item-summary-2026-08-04-v4"
```

**좋은 소식:** TypeScript 쪽은 이 값을 함수 소스(`render.toString()`)에서 파생했는데,
그건 의미가 아니라 **빌드 산출물**이라 번들러 설정만 바꿔도 버전이 흔들렸다.
Python 상수에는 그 취약점이 없다.

**나쁜 소식:** `CLAUDE.md §2-4` 가 금지한 바로 그 방식이다 —
"손으로 문자열을 고치는 방식은 반드시 잊어버린다." `app/prompts.py` 의 독스트링도
"프롬프트를 수정하면 반드시 이 값을 함께 올린다" 며 사람의 규율에 기대고 있다.

다만 **지금은 올리지 않는 것이 맞다.** 프롬프트 본문이 아직 안 옮겨졌고, 여기서 값을
바꾸면 이관 시점에 기존 `analysis_history` 가 전부 무효가 된다. 그 판단은 정확하다.

→ **프롬프트가 실제로 들어오는 시점(M2, `bid-summary`)에 파생 방식으로 바꾼다.**
재료는 프롬프트 문자열 + 모델 ID + 디코딩 파라미터. 함수 소스는 쓰지 않는다.

### F-3 — 단일 `promptVersion` 은 `bid-summary` 에서 깨진다

```python
return {"promptVersion": ITEM_SUMMARY_PROMPT_VERSION}
```

문자열 하나다. 그런데 `Principles §5.4` 는 `bid-summary` 의 프롬프트가 다른 것을
**계약이라고** 못박았다. `bid-summary` 가 들어오는 순간 두 프롬프트를 문자열 하나로
표현해야 하고, 백엔드는 `bid-summary` 결과를 `item-summary` 의 버전으로 캐시한다.

→ `CLAUDE.md §6 전제 F` 가 정확히 이 문제다. **M2 이전에** 정한다.
필드 추가는 자유이므로(`Principles §7.1`) 기존 `promptVersion` 을 두고
`versions: {엔드포인트: 버전}` 을 함께 내려보내면 백엔드 변경 없이 넘어갈 수 있다.

### F-4 — 미구현 응답이 `501` 인데, 백엔드가 그걸 폴백으로 바꾸는지 미확인 🔴

`app/main.py` 의 독스트링은 "200 으로 위장하면 폴백 결과가 캐시에 눌러앉는다" 고
적었다. **그 논증은 옳다.** 다만 답하는 질문이 다르다.

남은 질문: **`501` 이 백엔드의 `AiUnavailableException` 경로를 타는가?**
`ai-boundary.md §6.4` 의 200 폴백은 그 예외를 잡아서 만들어진다.
`501` 이 그 경로를 타지 않으면 프론트가 날것의 `501` 을 받고,
"AI 없이도 쓸 수 있는 문서 태그·법령 검토" 화면까지 같이 죽는다.

→ **해소(2026-08-06). `501` 은 폴백 경로를 탄다. 상태 코드를 바꾸지 않는다.**

`AiClient.post()`/`get()` 은 `RestClientException` 을 잡는데, 이는 `RestClient.retrieve()` 가
4xx·5xx 양쪽에 던지는 예외(`HttpClientErrorException`·`HttpServerErrorException`)의 **부모**다.
**status 로 분기하지 않는다** — 따라서 `501` 도 `AiUnavailableException` 이 되고
`ai-boundary.md §6.4` 의 200 폴백이 만들어진다.

같은 코드를 읽다가 **더 나쁜 것을 봤다 — 실패 본문이 아예 파싱되지 않는다.**
`e.getMessage()` 만 남기고 `code`·`retryable`·`requestId` 는 전부 버려진다.
F-1 은 "백엔드가 하나의 파서로 읽을 수 없다" 가 아니라 **"읽지 않는다"** 였다.
→ §3 의 2번이 **통보에서 요청(R0)으로 승격**된다.

### F-5 — 인용 검증: 빈 인용을 반드시 기각할 것 (아직 안 쓴 코드에 대한 메모)

`item-summary` 가 `NOT_PORTED` 라 인용 검증 코드는 아직 없다. 쓸 때 주의할 것:

**빈 문자열 `""` 는 어떤 원문에서도 "발견" 된다.** 축자 대조를
`quote in original_text` 나 `original[offset:offset+len] == quote` 로만 짜면
빈 인용이 통과하고, 백엔드의 원문 대조도 똑같이 통과시킨다 →
**근거 없는 사실이 채택된다.** `Principles §2.4` 가 조용히 무력화되는 경로다.

(TypeScript 초안에 실제로 있던 구멍이다. 같은 실수를 반복하지 않기 위해 적어 둔다.)

### F-6 — `/docs`·`/openapi.json` 이 인증을 우회한다

```python
OPEN_PATHS = {"/health", "/healthz", "/docs", "/openapi.json", "/redoc"}
```

`AI_SERVICE_SECRET` 을 설정해도 이 다섯은 통과한다. `/health` 는 그래야 맞지만
(오케스트레이터가 부른다), `/docs`·`/openapi.json`·`/redoc` 은 **인증 없이 API 표면
전체를 보여 준다.** 내부망 전용이면 실害는 작지만, 공개되는 순간 정보 노출이다.

→ 운영 환경에서는 `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` 로 끄거나
`OPEN_PATHS` 에서 뺀다.

---

## 2. 확정된 것

### D-A — 스택은 Python 이다

`origin/main` 의 Python 구현을 정식으로 삼는다. 취향이 아니라 세 가지 이유다:

1. 더 앞서 있고, **실제 모델에 붙는 것이 확인된** 유일한 구현이다.
2. `module_a`·`module_b`·`korean-law-mcp` 가 이미 Python 이다(`ai-boundary.md §2`).
   다른 언어를 고르면 이 프로세스들과 영원히 경계가 갈린다.
3. `README.md` 가 이미 프론트·백엔드 저장소에 그 스택을 공표했다.

### D-B — 운영용 `/health` 를 둔다

Python 구현에 이미 `/health`·`/healthz` 가 있다. 11개 계약 표면과 별개의
운영 표면이며, 오케스트레이터가 "살아 있음" 을 물을 곳이 필요하다.
`/api/ai/capacity` 는 "일할 수 있음" 을 답하는 다른 질문이다.

---

## 3. 백엔드와 합의가 필요한 목록

`ai-boundary.md §6` 은 현재 네 항목뿐이다. 최소한 다음이 더 필요하다:

**종류를 먼저 가른다.** 전부 "합의" 로 묶으면 읽으면 끝날 것이 답을 기다리며 멈춘다.
`읽기` = 백엔드 코드에 답이 있다 · `요청` = 백엔드가 코드를 바꿔야 한다 ·
`통보` = 필드 추가라 합의가 필요 없다(`Principles §7.1`). 보내는 계획은 `plan.md §8`.

| # | 항목 | 종류 | 출처 |
|---|---|---|---|
| 1 | **`AiClient` 가 `501 NOT_PORTED` 를 폴백으로 바꾸는가** | **읽기** | F-4 · 가장 시급 |
| 2 | 실패 본문에 `code`·`retryable`·`requestId` 를 싣는 것 | **통보** | F-1 · `docs/failure-modes.md` |
| 3 | `prompt-version` 을 엔드포인트별 맵으로 확장할지 | **통보** | F-3 · 전제 F |
| 4 | `embeddingVersion` 의 저장 여부 | **읽기** | Principles §3.4 |
| 5 | 엔드포인트 **× 업무구분**별 타임아웃과 리스 시간의 부등식 | **요청 R3** | Principles §4.2 · `plan.md §7` |
| 6 | 인용 좌표계 — 문자열 대조인가 offset 대조인가 | **읽기** | Principles §2.4 |
| 7 | `_analysisHistoryId` 의 생성 주체 (이력을 소유한 쪽이 맞다) | **읽기** | Principles §7.3 |
| 8 | 부분 결과 + 데드라인 초과 시 `200 degraded` 가 맞는가 | **요청 R4** | 전제 D · `failure-modes.md §4` |
| 9 | **`bsnsDivNm` 을 `item-summary`·`bid-summary` 입력에 실을 것** | **요청 R1** | `plan.md §5.2` |
| 10 | 첨부 선택 규칙을 업무구분별로 — `/규격서/` 파일명 게이트는 용역·공사에서 빈 결과를 낸다 | **요청 R2** | `plan.md §6` |

---

## 4. 아직 미확인인 전제

`CLAUDE.md §6` 중 남은 것:

| # | 전제 | 상태 |
|---|---|---|
| ~~A~~ | 기존 모듈이 이미 들어 있다 | **해소(맞음).** 원격에 있었다 — §0 |
| B | `item-summary` 4단계 · 첨부 파싱은 백엔드가 끝내고 넘겨줌 | 미확인. `item-summary` 가 바로 이것 때문에 막혀 있다 |
| C | `documentSignals` 분담 | 미확인 |
| D | 부분 결과 + 데드라인 초과 시 `200 degraded` | 미확인 |
| E | `_analysisHistoryId` 는 백엔드가 만든다 | 미확인 |
| F | `prompt-version` 엔드포인트별 맵 | 미확인. **M2 이전에 정해야 한다** — F-3 |
| ~~G~~ | 빌드·테스트·실행 명령 | **해소.** `CLAUDE.md §7` |
