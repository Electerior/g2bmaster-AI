# 이식 현황

`g2bmaster-backend/docs/ai-boundary.md` §5 의 11개 엔드포인트 중 **어디까지 왔고 무엇이 남았는지**를
숨김 없이 적는다. 백엔드 저장소의 `docs/porting-status.md` 와 같은 규칙이다 —
"곧 됩니다"는 쓰지 않고, 되는 것과 안 되는 것만 적는다.

기준 원본: `g2bmastersopen@d2fa9ada` (2026-08-05, `server.js` 5363줄 / `lib/` 52개 모듈).
마지막 갱신: 2026-08-26

---

## 요약

**2026-08-26 — 가격 표면을 폐기하고 요약을 하나로 합쳤다.**

| 엔드포인트 | 상태 |
|---|---|
| `GET /api/ai/prompt-version` | ✅ 완료 |
| `GET /api/ai/capacity` | ✅ 완료 |
| `GET /api/llm/models` | ✅ 완료 |
| `GET /api/ai/config` | ✅ 완료 (계약 밖 — 백엔드는 부르지 않는다) |
| `POST /api/embed` | ✅ 완료 (ML 스택 미설치 시 503 `EMBEDDING_UNAVAILABLE`) |
| **`POST /api/notice-summary`** | ✅ **완료 — 실측 확인.** `item-summary`·`bid-summary` 를 대체한다 |
| `POST /api/legal/review-clauses` | ❌ 501 `NOT_PORTED` |
| `POST /api/legal/outreach-draft` | ❌ 501 `NOT_PORTED` |
| `POST /api/pledge/revision-workflow` | ❌ 501 `NOT_PORTED` |

### 폐기한 것 (2026-08-26)

| 폐기 대상 | 사유 |
|---|---|
| `POST /api/price/resolve` · `/api/price/url` · `/api/estimate-unit-cost` · `/api/prebuilt-comparables` | AI 가격 탐색·원가추정 폐기 |
| `app/` 13모듈 약 3,100줄 (`price` `pricecommon` `enuri` `itmaya` `estimate` `prebuilt` `part_resolver` `product_index` `discover` `hardware_kb` `wiki` `browser_fetch` `spec_parser`) | 위 표면의 구현체. `spec_parser` 는 애초에 importer 가 없는 고아였다 |
| `POST /api/item-summary` · `/api/bid-summary` | `notice-summary` 하나로 합쳤다 |
| 실패코드 `PRICE_SOURCE_BROKEN` · `UNSUPPORTED_SOURCE` | 대상 표면이 사라졌다 |
| 설정 `searchProvider` `searchUrl` `searchKey` `searchPlatforms` `pricePrompt` `priceSources` | 〃 |
| `set_ai_config()` | 호출자가 0이었다 (`data/ai-config.json` 의 유일한 writer) |
| `docs/price-search.md` · `docs/backend-price-api.md` | 설계 문서 |

**백엔드·프론트에 아직 반영되지 않았다.** `AiClient` 는 여전히 `itemSummary`·`resolvePrice`
등을 들고 있고, 프론트는 존재하지 않는 `/api/bid-summary` 를 부른다. 두 저장소가 무엇을
철거해야 하는지는 **`docs/contract-break.md`** 에 적었다.

**미구현은 200 이 아니라 501 로 응답한다.** `AiClient.itemSummary` 는 `aiFallback` 응답을
성공으로 치지 않는데(ai-boundary.md §6.3), 미구현을 폴백처럼 200 으로 위장하면 그 결과가
`analysis_history` 에 눌러앉아 **영원히 재분석되지 않는다.**

---

## ✅ 완료

### LLM 연동 계층

원본 `lib/lms.js`(125줄) + `lib/llm-worker-pool.js`(195줄) + `lib/ai-config.js`(117줄) 이식.

- 설정 우선순위 `data/ai-config.json` > 환경변수 > 기본값 — 원본과 동일
- LM Studio `/api/v0/models` 로 로드 모델·컨텍스트 추적(TTL 10초), 외부 API 는 `/v1/models` 폴백
- 서버가 꺼져 있어도 `~/.lmstudio/models` 를 훑어 설치된 모델을 보여 준다(`ok:false` 로 정직하게)
- 워커 풀: 여유율 기반 분배, 재시도 가능 오류에만 쿨다운(30초) + **다른 워커로** 페일오버

실제로 확인했다 — 로컬 LM Studio(모델 4종)에 붙여 모델 목록·용량·헬스체크가 동작했고,
채팅 호출은 서버가 돌려준 사유까지 그대로 표면화됐다.

```
reachable  : True
models     : 4개 (qwen3.6-35b-a3b, llama-3.3-70b, qwen3.6-27b, gemma-4-31b-qat)
capacity   : 1
smoke_llm: SKIP — LLM 400: Failed to load model "qwen/qwen3.6-35b-a3b". Error: ... SIGABRT
```

> **원본에서 고친 것 하나.** 원본 `lms.js` 주석은 "LM Studio 가 400 을 돌려주면 그 400 이
> 예외로 올라가 화면엔 'LLM 서버에 연결하지 못해'로 둔갑했다(멀쩡히 켜져 있는데도)"고
> 적어 두었지만, 정작 그 400 의 본문을 읽지는 않았다. 이 이식본은 서버가 알려 준 사유를
> 예외 메시지에 실어 올린다(`server_reason`). 위 SIGABRT 메시지가 그 결과다.

### 실패 응답 계약

전 표면의 실패가 `app/errors.py` 한 곳에서 나온다 — `{code, error, retryable, requestId}`.
예전에는 갈래마다 본문이 달라(`{code,error,reason}` · `{error,path}` · `{error}` · `{detail:[...]}`)
백엔드가 하나의 파서로 읽을 수 없었다.

`docs/failure-modes.md §2` 의 표는 `scripts/test_errors.py` 가 파싱해 코드와 대조한다.
한쪽만 고치면 `make check` 가 깨진다.

> **아직 실효가 없다.** 백엔드 `AiClient` 는 `e.getMessage()` 만 남기고 이 본문을 읽지 않는다.
> 계약을 먼저 세워 둔 것이고, 파싱 요청은 `docs/decisions.md §3.1` 의 **R0** 이다.

### 프롬프트 버전

`item-summary-2026-08-04-v4` — 원본 `lib/analysis-history.js` 값 그대로다.
**프롬프트 본문을 옮기기 전까지 이 값을 올리지 않는다.** 올리는 순간 기존 분석 캐시가 전부 무효가 된다.

`GET /api/ai/prompt-version` 은 `promptVersion`(단일 값, `AiClient` 가 읽는 키)과
`versions`(엔드포인트별 맵)를 함께 낸다. 프롬프트가 업무구분별로 갈리면 문자열 하나로는
표현되지 않는다(`decisions.md F-3`).

### 임베딩

원본 `module_server.py` 의 `/api/embed` 를 그대로 쓴다. `module_a`·`module_b`·`korean-law-mcp` 는
원본에서 통째로 옮겨 왔다(이미 Python 이라 이식이 아니라 이동이다).

무거운 ML 스택은 `requirements-ml.txt` 로 분리했다. 없으면 서비스는 정상으로 뜨고
임베딩 경로만 503 을 준다 — 임베딩 하나 때문에 나머지가 막히면 안 된다.

### `POST /api/notice-summary` (2026-08-26)

`app/handlers/notice_summary_handler.py`. 원본 4스텝(clamp → facts → summary → items)을
**LLM 호출 한 번**으로 줄였다 — 가격 추정과 품목 추출을 폐기했으므로 그 단계가 존재할 이유가 없다.

실측: 픽스처 3건 전부 200. LM Studio 를 끄면 `503 LLM_UNAVAILABLE` +
`retryable:true`. 회차 기록은 `docs/summary-eval.md`.

> **한때 이 둘은 200 을 돌려주고 있었다.** payload 에 이름만 있으면
> `"…요약이 완료되었습니다"` 라는 지어낸 문장을 `aiFallback=false` 로 실어 보냈다.
> `AiClient` 는 `aiDisabled`/`aiFallback` 만 걸러내므로 그것을 **성공으로 판정**하고,
> `AnalysisJobRunner` 가 `analysis_history` 에 적재한 뒤 작업을 완료 처리한다 —
> 재사용 키가 같으므로 그 행은 영원히 재분석되지 않는다.
>
> `scripts/test_http_contract.py` 가 이제 그 반대를 건다: **LLM 이 닿지 않는 상태에서
> `/api/notice-summary` 가 200 을 내면 실패**다.

---

## ❌ 미착수

### 나머지 3개

| 엔드포인트 | 옮겨올 원본 | 규모 |
|---|---|---|
| `legal/review-clauses` | `lib/legal-review.js` + `lib/law-mcp.js` | 520줄 |
| `legal/outreach-draft` | `lib/legal-review.js` | (위와 공유) |
| `pledge/revision-workflow` | `lib/pledge-workflow.js` + `lib/pledge-revision.js` | 255줄 |

LLM 호출 계층이 준비됐으므로 이들은 프롬프트와 후처리 이식만 남았다.
`korean-law-mcp` 는 이미 이 저장소에 들어와 있어 법령 검토는 MCP 연결만 붙이면 된다.

---

## 검증

```bash
make check
```

| 스크립트 | 무엇을 막는가 |
|---|---|
| `test_worker_pool.py` | 워커 분배·쿨다운·페일오버 회귀 (GPU 한 대가 죽었을 때 처리량이 0 이 되는 상황) |
| `test_errors.py` | 실패 한 건의 모양 — 문서 표와 코드의 어긋남, 재시도 가능한 4xx, `requestId` 유실, `detail` 유출 |
| `test_http_contract.py` | 계약 11개 경로 누락, 미구현의 200 위장, 프롬프트 버전 변경, 호출자 인증 |
| `smoke_llm.py` | 실제 LLM 서버 연동 (없으면 SKIP) |

`test_http_contract.py` 는 개발 PC 에 LM Studio 가 떠 있어도 결과가 흔들리지 않도록
닫힌 주소로 고정해 돈다. 라이브 확인은 `smoke_llm.py` 가 따로 한다.

### `make check` 밖에 있는 것

이 문서는 **HTTP 표면의 이식 현황**만 다룬다. 아래 둘은 표면이 아니므로 위 표에 없다.

| | 무엇 | 검증 |
|---|---|---|
| `bidpipe/` | 조달 공고 마진 분석 파이프라인. 2026-08-25 Electerior 에서 이전 | `make bidpipe-check` · `bidpipe-fidelity` · `bidpipe-fixture` |
| `app/opportunity.py` | 사업기회 분류(LLM). 엔드포인트가 아니다 | 없음. 실측 이력은 `docs/opportunity-eval.md` |
