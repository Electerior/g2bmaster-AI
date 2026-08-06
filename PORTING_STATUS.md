# 이식 현황

`g2bmaster-backend/docs/ai-boundary.md` §5 의 11개 엔드포인트 중 **어디까지 왔고 무엇이 남았는지**를
숨김 없이 적는다. 백엔드 저장소의 `docs/porting-status.md` 와 같은 규칙이다 —
"곧 됩니다"는 쓰지 않고, 되는 것과 안 되는 것만 적는다.

기준 원본: `g2bmastersopen@d2fa9ada` (2026-08-05, `server.js` 5363줄 / `lib/` 52개 모듈).
마지막 갱신: 2026-08-06

---

## 요약

| 엔드포인트 | 상태 |
|---|---|
| `GET /api/ai/prompt-version` | ✅ 완료 |
| `GET /api/ai/capacity` | ✅ 완료 |
| `GET /api/llm/models` | ✅ 완료 |
| `POST /api/embed` | ✅ 완료 (ML 스택 미설치 시 503 `EMBEDDING_UNAVAILABLE`) |
| `POST /api/item-summary` | ❌ 501 `NOT_PORTED` — 백엔드 첨부 파싱 대기 |
| `POST /api/bid-summary` | ❌ 501 `NOT_PORTED` |
| `POST /api/legal/review-clauses` | ❌ 501 `NOT_PORTED` |
| `POST /api/legal/outreach-draft` | ❌ 501 `NOT_PORTED` |
| `POST /api/pledge/revision-workflow` | ❌ 501 `NOT_PORTED` |
| `POST /api/price/resolve` | ❌ 501 `NOT_PORTED` |
| `POST /api/price/url` | ❌ 501 `NOT_PORTED` |

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

### 프롬프트 버전

`item-summary-2026-08-04-v4` — 원본 `lib/analysis-history.js` 값 그대로다.
**프롬프트 본문을 옮기기 전까지 이 값을 올리지 않는다.** 올리는 순간 기존 분석 캐시가 전부 무효가 된다.

### 임베딩

원본 `module_server.py` 의 `/api/embed` 를 그대로 쓴다. `module_a`·`module_b`·`korean-law-mcp` 는
원본에서 통째로 옮겨 왔다(이미 Python 이라 이식이 아니라 이동이다).

무거운 ML 스택은 `requirements-ml.txt` 로 분리했다. 없으면 서비스는 정상으로 뜨고
임베딩 경로만 503 을 준다 — 임베딩 하나 때문에 나머지가 막히면 안 된다.

---

## ❌ 미착수

### `POST /api/item-summary` — 백엔드에 막혀 있다

심층 분석은 **첨부 원문(Markdown)과 문서 신호**를 입력으로 받는다. 그 입력을 만드는
백엔드의 첨부 파싱(HWP·PDF·ZIP)이 아직 이식되지 않았다
(`g2bmaster-backend/docs/porting-status.md`: "첨부 파싱 ❌ 미착수").

계약된 입력이 존재하지 않는 상태에서 구현하면 실제와 다른 입력을 가정하게 된다.
백엔드 첨부 파싱이 들어온 뒤에 착수한다.

관련해서 확인해 둘 것 — ai-boundary.md §4 는 `procurement-analysis.js` 를 반으로 가른다.
`analyzeProcurementMarkdown`(LLM 호출)은 이쪽, **근거 인용 검증**(`evidence.quote` 가 원문에
문자 그대로 있는지)은 백엔드다. 현재 백엔드에 그 검증이 아직 없다.

### 나머지 6개

| 엔드포인트 | 옮겨올 원본 | 규모 |
|---|---|---|
| `bid-summary` | `lib/bid-summary.js` | 210줄 |
| `legal/review-clauses` | `lib/legal-review.js` + `lib/law-mcp.js` | 520줄 |
| `legal/outreach-draft` | `lib/legal-review.js` | (위와 공유) |
| `pledge/revision-workflow` | `lib/pledge-workflow.js` + `lib/pledge-revision.js` | 255줄 |
| `price/resolve` · `price/url` | `price-web.js` | 671줄 |

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
| `test_http_contract.py` | 계약 11개 경로 누락, 미구현의 200 위장, 프롬프트 버전 변경, 호출자 인증 |
| `smoke_llm.py` | 실제 LLM 서버 연동 (없으면 SKIP) |

`test_http_contract.py` 는 개발 PC 에 LM Studio 가 떠 있어도 결과가 흔들리지 않도록
닫힌 주소로 고정해 돈다. 라이브 확인은 `smoke_llm.py` 가 따로 한다.
