# 실행 계획 — 2026-08-06

**이 문서는 권위가 없다.** 결정은 `decisions.md`, 진척은 `PORTING_STATUS.md` 가 소유한다.
여기는 **무엇을 어떤 순서로 왜 그 순서로 하는가**만 적는다. 둘과 어긋나면 저쪽이 이긴다.

계획이 실행되면 해당 절은 지우고 `PORTING_STATUS.md` 에 결과만 남긴다.
"곧 됩니다"를 쓰지 않는 규칙은 여기에도 적용된다 — 각 단계에 **DoD** 가 없으면 그 단계는 계획이 아니다.

---

## 0. 지금 어디에 있나

- 11개 표면 중 4개 완료(`prompt-version`·`capacity`·`models`·`embed`), 7개 `501 NOT_PORTED`
- LLM 계층은 실물 확인됨(LM Studio 모델 4종, 워커 풀 쿨다운·페일오버)
- **`dev` 에 TypeScript 병렬 구현 2,946줄이 남아 있다.** `decisions.md §0` 은 제거했다고 적었지만 제거되지 않았다
- **실물 규격서 코퍼스가 0건이다.** `g2bmastersopen/data/attachment_cache.db` 는 비어 있다

막고 있는 것은 코드가 아니라 두 가지다 — **실패 계약이 다섯 갈래인 것**, 그리고 **검증할 실물이 없는 것**.

---

## 1. M0.5 — 실패 계약을 하나로 (F-1 · F-4)

가장 먼저인 이유: 뒤에 오는 모든 것이 실패를 이 모양으로 보고한다.
`route` 스텝이 만들 `DIVISION_UNKNOWN` 을 실을 표 자체가 아직 없다.

### 1.1 `app/errors.py` 신설

`src/http/errors.ts` 를 옮긴다. 표 8종 + `AiFailure` + `to_http`.
`docs/failure-modes.md §2` 가 이미 언어 중립으로 같은 표를 갖고 있으므로 **값은 그 문서를 따른다.**

`failure-modes.md:3` 의 "단일 출처는 코드(`src/http/errors.ts`)" 를 `app/errors.py` 로 고친다.

### 1.2 실패 본문 다섯 갈래 → 하나

| 지금 | 바꾼 뒤 |
|---|---|
| `not_ported()` 501 `{code,error,reason}` | `AiFailure` 핸들러 |
| `embed` 503 `{code,error}` | 〃 |
| 404 `{error,path}` | 404 핸들러 → 표준형 |
| 401 `{error}` | 미들웨어에서 표준형 |
| **422 `{detail:[...]}`** | `@app.exception_handler(RequestValidationError)` |

`src/app.ts:104` 의 `isCallerError` 논리를 함께 옮긴다 —
**깨진 JSON 본문이 500(재시도 가능)으로 분류되면 백엔드가 절대 성공할 수 없는 요청을 재시도 예산이 다할 때까지 반복한다.**

### 1.3 `requestId`

`src/http/requestId.ts` 그대로. 본문 → `X-Request-Id` → uuid 순서.
두 저장소의 로그를 잇는 유일한 실이라 여기서 새로 만들면 장애 조사가 불가능해진다.

### 1.4 `NOT_PORTED` 501 유지 — 해소됨

`AiClient` 를 읽어 확인했다(`decisions.md F-4`). `RestClientException` 을 잡으므로
**status 로 분기하지 않고, `501` 도 폴백 경로를 탄다.** 503 으로 바꾸지 않는다.

→ `failure-modes.md §3`(503 을 제안하던 절)과 `D-002` 재발행 계획은 **철회한다.**
대신 그 절을 "왜 501 이어도 되는가" 로 다시 쓴다.

**같은 읽기에서 나온 더 나쁜 것** — 백엔드는 실패 본문을 **파싱하지 않는다.**
`code`·`retryable`·`requestId` 를 실어도 지금은 버려진다. §1.2 는 여전히 해야 하지만
(우리 쪽 일관성), 실효를 보려면 §8.2 의 **R0** 가 함께 가야 한다.

**DoD** — `scripts/test_http_contract.py` 가 다섯 갈래 전부에서 `{code, error, retryable, requestId}` 를 확인하고 통과.

---

## 2. M0.6 — 문서 정합 · TypeScript 제거

M0.5 이후여야 한다. 실패표를 Python 으로 옮기기 전에 `src/` 를 지우면
`failure-modes.md`(권위 4순위)가 잠시 아무것도 가리키지 않는다.

### 2.1 남길 것 (이식 완료 확인 후 삭제)

| 원본 | 목적지 | 시점 |
|---|---|---|
| `src/http/errors.ts` | `app/errors.py` | M0.5 |
| `src/http/requestId.ts` | 미들웨어 | M0.5 |
| `src/app.ts:54-110` | 예외 핸들러 3종 | M0.5 |
| `src/pipeline/kernel.ts` | `app/pipeline/kernel.py` | **M2 직전** |
| `promptRegistry.versionMap()` 개념 | `app/prompts.py` | M0.7 |
| `test/` 1,475줄의 **케이스 목록** | `scripts/test_http_contract.py` | M0.5 |

### 2.2 버릴 것

- `src/` · `test/` · `tsconfig.json` · `vitest.config.ts` · `package.json` · `package-lock.json`
- `gateway.ts` 의 Semaphore·AbortController — `app/llm/worker_pool.py` 가 실물로 더 잘한다
- `promptRegistry` 의 `render.toString()` 파생 — **빌드 산출물이라 번들러 설정만 바꿔도 버전이 흔들린다**(F-2)
- `item-summary/index.ts` — 전제 B 미확인 상태에서 쓴 추측 코드. 재작성이 이식보다 싸다
- `clamp.ts` · `citation.ts` — 상수(1.8 · 1500 · 50000)는 `Principles.md §3.5` 와 `api-contract.md` 에 이미 있고,
  빈 인용 기각은 `decisions.md F-5` 에 있다. **지식이 문서에 있으므로 파일은 필요 없다**

원본은 커밋 `95eeec0` 에 온전히 남는다.

### 2.3 문서 수정

- `decisions.md §0` "제거했다" → 사실로 수정 · **D-002 재발행**
- `README.md:12` 스택 표기("원본의 Node LLM 모듈") → Python
- `.gitignore` 의 `node_modules/`·`coverage/`·`dist/` 3줄 제거

**DoD** — `git ls-files` 에 `.ts` 0건. `make check` 통과. `failure-modes.md` 가 실재하는 파일을 가리킴.

---

## 3. M0.7 — 프롬프트 버전 (F-3)

`{promptVersion, versions}` 를 함께 내보낸다. 기존 필드를 남기므로 백엔드 변경 없이 넘어간다.

**키 설계가 §5 때문에 바뀌었다.** 엔드포인트당 하나가 아니다:

```
versions = {
  "item-summary:규격서":   "...",
  "item-summary:과업지시서": "...",
  "bid-summary":          "...",
}
```

프롬프트가 **엔드포인트 × 문서종류**로 갈리므로, 단일 문자열은 `bid-summary` 가 아니라
**첫 업무구분 분기에서 이미 깨진다.**

버전 파생은 M2 에서 `sha256(프롬프트 문자열 + 모델 ID + 디코딩 파라미터)`.
프롬프트 본문이 없는 지금은 상수를 유지한다 — 올리는 순간 기존 `analysis_history` 가 전부 무효가 된다.

**DoD** — `GET /api/ai/prompt-version` 이 두 형태를 함께 반환. 계약 테스트가 기존 필드 존재를 고정.

---

## 4. M1 — 코퍼스 수집 (코드보다 먼저)

**실물 규격서가 0건이다.** 4벌 프롬프트를 설계해도 검증할 방법이 없다.

- 나라장터 API 로 업무구분별 공고 각 10건 + 첨부 수집
- 저장 위치는 이 저장소 밖. 내구 상태를 갖지 않는다(CLAUDE.md §2-10)
- 수집 항목: 파일명 · 확장자 · 변환 MD · 선언 `bsnsDivNm`
- **외자 입찰공고 API 오퍼레이션 존재 여부를 여기서 확인한다.** 원본은 발주계획(`Frgcpt`)에서만 부른다

**DoD** — 업무구분 4종 × 10건의 변환 MD 와 정답 라벨(`docKind`). 이후 모든 전략의 골든셋이다.

---

## 5. M2 — `bid-summary` + 라우팅 + 물품 전략

### 5.1 커널

`src/pipeline/kernel.ts` → `app/pipeline/kernel.py`.
`{ok|degrade|fatal}` · `Deadline` · `Trace`. 예외로 대체하면 "계속 갈 수 있음" 과 "여기서 끝" 의 구분이 사라진다.

### 5.2 분류 축은 둘이다

| 축 | 값 | 정하는 주체 | 쓰임 |
|---|---|---|---|
| `division` | 물품·용역·공사·외자 | **백엔드**(`bsnsDivNm`, G2B 원본값) | 검증·리스크 신호. 우리가 뒤집지 않는다 |
| `docKind` | 규격서·과업지시서·설계서·외자유의서 | **우리** | **추출 전략을 고르는 값** |

전략을 `division` 으로 고르면 안 된다. 물품 공고에 과업지시서가 붙는 경우(설치·유지보수 포함)가 흔하고,
그때 `item×quantity` 프롬프트를 넣으면 **에러 없이 빈약한 결과**가 나온다.

**불일치 자체가 산출물이다** — `divisionMismatch` 로 보고한다. `degraded` 가 아니다.
"물품 공고인데 과업지시서가 붙음 = 용역성 과업 혼재" 는 영업 판단에 직접 쓰인다.

### 5.3 스텝 순서

```
route(required) → clamp → facts → summary → items → legal
```

`route` 는 `docKind` 판정에 LLM 을 최대 1회 쓴다(파일명 순위로 랭킹 → 최고점 1건만 확인).
판정 실패 시 **물품으로 가정하지 않고** `fatal(DIVISION_UNKNOWN)` —
잘못된 전략으로 돌린 결과가 캐시에 눌러앉는 것이 빈 결과보다 나쁘다.

### 5.4 전략은 코드가 아니라 표다

`app/strategy.py` 한 파일. 4개 모듈로 나누면 구현이 하나뿐인 인터페이스가 4개 생긴다.

```python
@dataclass(frozen=True)
class Strategy:
    doc_kinds: tuple[str, ...]
    fact_kinds: tuple[str, ...]
    prompt: str
    clamp: str                    # 'table_first' | 'prose_only'
    parse_first: tuple[str, ...]  # LLM 전에 파서가 먹을 확장자
    skip_steps: frozenset[str]
    deadline_ms: int
```

진짜 코드가 필요한 것은 **공사 물량내역서 XLSX 파서** 하나뿐이다. 나머지는 데이터다.

### 5.5 새 실패 코드 2개

| code | status | retryable | 언제 |
|---|---|---|---|
| `DIVISION_UNKNOWN` | 400 | ✗ | 선언값도 없고 문서로도 판정 불가 |
| `DOC_KIND_UNRESOLVED` | — | — | degrade. 전략은 골랐으나 확신 없음 |

`failure-modes.md §2` 에 추가한다.

**DoD** — 코퍼스 물품 10건에서 `docKind` 판정 확인 + `bid-summary` 계약 테스트 통과.
**나머지 3종은 이 단계에서 `degrade` 로 정직하게 축소한다.** 빈약한 결과를 내보내지 않는다.

---

## 6. M3~ — 용역 · 공사 · 외자

각 구분은 **코퍼스 10건이 모인 뒤에만** 켠다.

### 물품 — 표가 곧 사실
- `clamp`: **`table_first`.** 마크다운 표를 행 중간에서 자르면 수량이 통째로 사라진다
- `fact_kinds`: `item{quantity,specs}` · `delivery` · `qualification`
- 검증: 표 행 수 대비 `item` 수. 표 20행에 item 3개면 `degrade`
- 고유 신호: 규격 특정(모델명·독점 인증) → 독소조항 후보로 백엔드에 넘김

### 용역 — 배점표가 최고 가치 정보
- 문서 우선순위: **제안요청서 > 과업지시서** (규격서는 없을 수도 있다)
- `fact_kinds`: `task` · `deliverable` · `manpower{등급,M/M,상주}` · `evaluation{항목,배점}` · `period`
- 검증: **배점 합계 = 100 ± 오차.** 안 맞으면 배점표를 잘못 읽은 것
- `skip_steps`: `items` — 품목·수량 개념이 없다
- 협상에 의한 계약의 승패는 기술:가격 비율에서 갈린다

### 공사 — LLM 에 넣으면 안 되는 것이 있다
- `parse_first`: `.xlsx` — 물량내역서 수천 행을 LLM 에 넣으면 토큰을 태우고 숫자를 환각한다
- `clamp`: `prose_only` — 내역서는 클램프 대상 제외. 공사시방서·현장설명서만 LLM
- `fact_kinds`: `work_type` · `quantity_item` · `period` · `site_condition` · `license`
- 고유 신호: **추정가 100억 경계**(종합심사 vs 적격심사) — 참여 판단이 완전히 갈린다

### 외자 — 서류 하나 빠지면 입찰무효
- 문서: 규격서 + **외자입찰유의서** 둘 다
- `fact_kinds`: `item` · `incoterms` · `shipment` · `origin` · `required_document`
- 검증: `incoterms` **enum 대조**(FCA/FOB/CPT/CFR/CIP/CIF/DAP/DDP). 목록 밖이면 기각
- 고유 산출물: `required_document` 체크리스트 — 공급자·제작자증명서 누락 = 입찰무효.
  **가장 값비싼 실수를 막는다**
- 공고 40일 전 규칙으로 마감 역산 가능

---

## 7. 데드라인은 구분별로 다르다

공사 공고는 첨부가 수십 MB, 물품은 한 건이다.
단일 데드라인이면 공사에서 `LLM_TIMEOUT` 이 상시화되고 물품에서는 리스를 쓸데없이 오래 잡는다.

```
물품 20s · 외자 30s · 용역 45s · 공사 60s
```

`AI 자체 데드라인 < g2b.ai.timeout-ms < 백엔드 리스` 부등식이 **구분별로 각각** 성립해야 한다.

---

## 8. 백엔드 제안 계획

### 8.0 원칙 — 묻기 전에 읽는다

`decisions.md §3` 은 10개를 "합의 필요" 로 묶어 두었지만, **그중 넷은 합의 사항이 아니라
백엔드 코드를 읽으면 나오는 사실이다.** 사실을 질문으로 보내면 답을 기다리는 동안 멈추고,
진짜 요청 네 개가 거기 묻혀 함께 늦어진다.

`gh` 가 `Electerior` 로 인증돼 있으므로 `gh api repos/Electerior/g2bmaster-backend/contents/...`
로 직접 읽을 수 있다. 저장소를 클론할 필요도 없다.

### 8.1 읽어서 해소한다 — 사람을 기다리지 않는다

| 항목 | 읽을 곳 | 해소되면 |
|---|---|---|
| **F-4** — `501` 이 `AiUnavailableException` 경로를 타나 | `integration/ai/AiClient` | M0.5 §1.4 의 "확정하지 않는다" 가 사라진다 |
| **전제 B** — `documents[].text` 로 넘어오나 | `AiClient.itemSummary` 요청 DTO | `item-summary` 입력 스키마 확정 |
| **전제 C** — `documentSignals` 분담 | AI 컨트롤러 응답 조립부 | 응답 스키마 확정 |
| **전제 E** — `_analysisHistoryId` 생성 주체 | `AnalysisJobRunner` | 응답에서 넣을지 뺄지 |
| **인용 대조** — 문자열이냐 offset 이냐 | 원문 대조 코드(있다면) | `citation` 설계 확정. 없으면 그것도 사실이다 |

**가장 시급한 F-4 가 이 표에 있다.** 질문 목록에 올려 두고 기다릴 일이 아니다.

**DoD** — `decisions.md §4` 의 미확인 전제가 B~F 다섯에서 줄어든다. 읽어서 확인한 것은 §1 에 근거와 함께 적는다.

### 8.2 요청 5건 — 백엔드 코드가 바뀌어야 하는 것

§8.1 을 실제로 읽은 결과 **하나가 통보에서 요청으로 승격됐다(R0).**
백엔드는 실패 본문을 파싱하지 않는다 — 필드를 실어도 지금은 버려진다.

| # | 요청 | 왜 백엔드인가 | 우리 우회 | 막히는 때 |
|---|---|---|---|---|
| **R0** | 실패 본문의 `code`·`retryable` 을 읽어 달라 | `catch (RestClientException e)` 가 `e.getMessage()` 만 남긴다 | 이미 그 모양으로 내보내는 중(무해) | 미구현 표면이 열릴 때까지 헛재시도 지속 |
| **R1** | `bsnsDivNm` 을 `item-summary`·`bid-summary` 입력에 실어 달라 | G2B 원본값은 백엔드 데이터에만 있다 | 없으면 `DIVISION_UNKNOWN` 으로 fatal. **물품으로 가정하지 않는다** | **M2** |
| **R2** | 첨부 선택 규칙을 업무구분별로 — `/규격서/` 파일명 하드 게이트는 용역·공사에서 빈 결과를 낸다 | 첨부 파싱은 백엔드 소유(CLAUDE.md §4) | `documents[]` 에 온 것만으로 `docKind` 판정 | **M3** |
| **R3** | 엔드포인트 × 업무구분별 타임아웃·리스 시간 | 리스는 백엔드 큐 소유. 부등식이 한쪽만으로 성립하지 않는다 | 자체 데드라인만 구분별로 적용 | **M2** |
| **R4** | `degraded: true` 를 **성공으로 캐시하지 말 것** (재시도 대상으로 둘 것) | 캐시·재시도 장부가 백엔드 소유 | `failure-modes.md §4` 대로 내보내되 채택은 백엔드에 달렸다 | **M3** |

### 8.3 통보 2건 — 허락을 구하지 않는다

필드 추가는 합의 없이 자유다(`Principles §7.1`). 허락을 구하면 불필요하게 막힌다.

- **실패 본문에 `code`·`retryable`·`requestId`** — "이제 이 모양으로 온다, 파서를 여기로 맞춰라"
- **`prompt-version` 이 `{promptVersion, versions}` 병행** — 기존 필드를 남기므로 백엔드는 아무것도 안 해도 된다

### 8.4 형식 — 산문이 아니라 패치

`ai-boundary.md` 는 **백엔드 저장소 소유이자 최상위 권위**다. 요청의 올바른 형태는
위시리스트가 아니라 **그 문서에 대한 diff** 다.

각 항목에 세 줄을 반드시 붙인다:

```
현재 우회:  (답이 없어도 우리가 무엇으로 진행 중인가)
답이 오면:  (무엇이 어떻게 바뀌는가)
막히는 때:  (어느 마일스톤에서 정말 멈추는가)
```

우회를 적으면 두 가지가 동시에 풀린다 — 우리는 답을 기다리며 멈추지 않고,
백엔드는 **진짜 긴급도**를 안다. 전부 급하다고 쓰면 전부 안 급해진다.

### 8.5 순서

1. **읽기**(§8.1) — 오늘. 아무에게도 물을 필요 없다
2. 읽어서 확인한 것을 `decisions.md §1` 에 근거와 함께 기록하고 `§4` 에서 지운다
3. 남은 **R1~R4 만** 이슈 하나로 — `gh issue create --repo Electerior/g2bmaster-backend`.
   **외부로 나가는 것이므로 본문 확인 후 발송한다**
4. 통보 2건(§8.3)은 M0.5 구현과 함께 같은 이슈에 코멘트로

읽기가 먼저인 이유는 순서상 편해서가 아니다 — **넷을 지우고 나면 이슈가 네 줄이 되고,
네 줄짜리 요청은 답이 온다.** 열 줄짜리는 통째로 "검토하겠다" 로 묶인다.

---

## 9. 하지 않는 것

- 업무구분 자동 추론 — 선언값(`bsnsDivNm`)이 있다
- 4개 전략 모듈 분리 — 표 하나로 충분하다
- 공사 도면(dwg) 해석
- 첨부 다운로드·텍스트 추출 — 백엔드 소유(CLAUDE.md §4)
- `evidence.quote` 최종 채택 판정 — 백엔드가 원문 대조로 한다. 우리는 그 검증을 쉽게 만들 뿐
