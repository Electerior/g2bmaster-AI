# g2bmaster-AI

나라장터(G2B) 입찰정보의 **추론 계층** — LLM 공고 요약·임베딩.

> **가격 기능은 2026-08-26 에 폐기했다.** AI 추정이든 단가 DB 든 다시 만들지 않는다.
> 프론트·백이 걷어내야 할 목록은 [`docs/contract-break.md`](docs/contract-break.md).

모놀리스 [`g2bmastersopen`](https://github.com/Electerior/g2bmastersopen) 를 세 저장소로
나눈 것 중 하나다.

| 저장소 | 역할 | 스택 |
|---|---|---|
| [`g2bmaster-frontend`](https://github.com/Electerior/g2bmaster-frontend) | 화면 | React 18 + TypeScript + Vite |
| [`g2bmaster-backend`](https://github.com/Electerior/g2bmaster-backend) | API·적재·영속 | Spring Boot 4.1 + MySQL 8 |
| **`g2bmaster-AI`** (이 저장소) | 추론 | Python 3 + FastAPI (`docs/decisions.md` D-A) |

---

## 실행

```bash
make install     # 임베딩까지 쓰려면 make install-ml
cp .env.example .env
make start       # http://127.0.0.1:8000
```

백엔드가 요구하는 것은 하나뿐이다 — **`AI_BASE_URL`(기본 `http://localhost:8000`)에서
HTTP 로 응답할 것.** 언어도 프레임워크도 백엔드는 모른다.

```bash
cd ../g2bmaster-backend && AI_ENABLED=true AI_BASE_URL=http://localhost:8000 ./mvnw spring-boot:run
```

**남은 미이식은 3개다**(`legal/review-clauses` · `legal/outreach-draft` ·
`pledge/revision-workflow`). 이들은 501 `NOT_PORTED` 로 정직하게 응답한다 —
무엇이 되고 무엇이 안 되는지는 [`PORTING_STATUS.md`](PORTING_STATUS.md) 를 볼 것.

AI 없이도 백엔드는 돈다. `AI_ENABLED=false` 로 두면 검색·트렌드·저장 공고·운영 화면은
전부 정상 동작한다(범위는 `g2bmaster-backend/docs/ai-boundary.md` §7).

```bash
cd ../g2bmaster-backend && AI_ENABLED=false ./mvnw spring-boot:run
```

### 검증 — 직접 돌리는 법

테스트는 **두 갈래**다. 성격이 다르니 섞어 읽지 말 것.

| | 명령 | LLM 필요 | 언제 |
|---|---|---|---|
| **계약** | `make check` | ❌ | 커밋 전 항상. 초록이 아니면 커밋하지 않는다 |
| **연기(smoke)** | `make smoke` · `make notice` | ✅ | 요약 품질을 볼 때. 프롬프트를 고쳤을 때 |

`make check` 는 LLM 을 **일부러 닫힌 주소로 고정**해서 돈다. 개발 PC 에 LM Studio 가
떠 있으면 결과가 달라지는데, 계약 검증이 환경에 흔들리면 신호가 아니라 잡음이 된다.

#### 1. 준비 (처음 한 번)

```bash
make install                 # 임베딩까지 쓰려면 make install-ml
cp .env.example .env
```

`make install-ml` 을 건너뛰면 임베딩 테스트 3건이 `skip` 으로 나온다. **정상이다** —
서비스는 뜨고 `/api/embed` 만 503 을 준다.

#### 2. 계약 테스트 — LLM 없이, 항상

```bash
make check                   # 아래 5개를 순서대로 돈다
```

| 타깃 | 무엇을 보나 |
|---|---|
| `make pool` | 워커 풀 분배·쿨다운·페일오버 |
| `make errors` | 실패 한 건의 본문 모양이 `docs/failure-modes.md` 표와 맞는지 |
| `make contract` | 경로가 전부 존재하는지, 폐기 경로가 되살아나지 않았는지 |
| `make extractor` | `module_b` 스키마·근거 좌표·프롬프트 규칙 |
| `make embedding` | 청크 자르기·상주 구조 (ML 스택 없으면 skip) |

끝에 `전부 통과` 가 나오고 종료 코드가 0 이어야 한다.

```bash
make check; echo "exit=$?"   # exit=0 이어야 한다
```

#### 3. 공고 요약을 눈으로 확인 — LLM 필요

**LM Studio 를 먼저 띄운다.**

```bash
lms server start             # 포트 1234
lms ps                       # 모델이 로드돼 있어야 한다. 없으면:
lms load qwen/qwen3.6-35b-a3b
```

```bash
make notice                  # 픽스처 3건(물품·용역·공사)을 요약해 보여 준다
```

요약 본문이 그대로 찍히고, 끝에 `docs/summary-eval.md` 에 붙일 표가 나온다.

**실제 공고로 돌리려면** 같은 모양의 JSON 을 만들어 인자로 준다.

```bash
python scripts/smoke_notice_summary.py 내공고.json
```

```json
{
  "bidNtceNo": "20260812345-00",
  "title": "정보시스템 서버 및 스토리지 장비 구매",
  "agency": "조달청",
  "amount": 120000000,
  "documents": [{ "name": "규격서.hwp", "text": "1. 사업개요 ..." }]
}
```

**뜬 서버로 쏘려면** `--base` 를 준다(포트는 `.env` 의 `PORT`).

```bash
make start &                              # 다른 터미널이 편하다
python scripts/smoke_notice_summary.py --base http://127.0.0.1:8000
```

`make smoke` 는 요약이 아니라 **LLM 연결 자체**(모델 목록·용량·채팅 1회)를 본다.
요약이 이상할 때 "모델이 문제냐 프롬프트가 문제냐"를 가르는 데 쓴다.

#### 4. 결과 읽는 법

| 끝줄 | 뜻 | 종료 코드 |
|---|---|---|
| `OK` | 통과 | 0 |
| `SKIP` | LM Studio 가 없거나 모델이 로드되지 않았다. **실패가 아니다** | 0 |
| `FAIL` | 진짜 실패 | 1 |

연기 테스트가 `FAIL` 로 잡는 것은 두 가지다.

- **요약이 비었는데 200** — 호출부가 "요약이 없는 공고"로 오해한다.
- **`promptVersion` 불일치** — 프롬프트만 고치고 `app/prompts.py` 의 버전을 안 올린
  경우다. 그대로 두면 백엔드가 낡은 분석 결과를 계속 재사용한다.

#### 5. 프롬프트를 고쳤다면

1. `app/prompts.py` 의 `NOTICE_SUMMARY_PROMPT` 를 고친다
2. **같은 파일의 `NOTICE_SUMMARY_PROMPT_VERSION` 을 반드시 올린다**
3. `make notice` 로 세 픽스처를 다시 돌린다
4. 출력을 **이전 회차와 항목 단위로 대조한다** — 서식을 고치는 변경이 내용을 지울 수 있다
   (실제로 v3 에서 금액 줄이 사라진 적이 있다: `docs/summary-eval.md`)
5. 끝에 나온 표를 [`docs/summary-eval.md`](docs/summary-eval.md) 에 한 줄 추가한다

---

## 무엇을 만들어야 하나

계약 전문은 `g2bmaster-backend/docs/ai-boundary.md` 에 있다. 다만 그 문서의 11개 표면
목록은 **낡았다** — 2026-08-26 에 가격 4개를 폐기하고 요약 2개를 하나로 합쳤다.
현재 표면과 계약 파기 내역은 [`docs/contract-break.md`](docs/contract-break.md).

| 메서드 | 경로 | 용도 | 상태 |
|---|---|---|---|
| POST | `/api/notice-summary` | 공고 요약 (LLM 호출 1회) | ✅ |
| POST | `/api/embed` | 텍스트 임베딩 (유사도 계산은 백엔드가 한다) | ✅ |
| GET | `/api/ai/prompt-version` | 분석 재사용 키에 들어가는 프롬프트 버전 | ✅ |
| GET | `/api/ai/capacity` | 워커 용량 (내보내기 ETA 계산용) | ✅ |
| GET | `/api/llm/models` | 모델 목록·도달 여부 (시스템 화면) | ✅ |
| GET | `/api/ai/config` | 설정 조회 — 읽기 전용 | ✅ |
| POST | `/api/legal/review-clauses` | 조항 위법성 검토 | ❌ 501 |
| POST | `/api/legal/outreach-draft` | 콜드메일 초안 | ❌ 501 |
| POST | `/api/pledge/revision-workflow` | 서약서 수정본 생성 | ❌ 501 |

**폐기(2026-08-26).** `/api/item-summary` · `/api/bid-summary` · `/api/price/resolve` ·
`/api/price/url` · `/api/estimate-unit-cost` · `/api/prebuilt-comparables`

원본에서 이쪽으로 넘어오는 모듈: `lib/lms.js`, `lib/llm-worker-pool.js`,
`lib/law-mcp.js`, `lib/legal-review.js`, `lib/pledge-workflow.js`,
`lib/pledge-revision.js`, `lib/ai-config.js`, `price-web.js`,
그리고 `module_a/` · `module_b/` · `korean-law-mcp/` 일체.

### 먼저 확인할 네 가지

`ai-boundary.md` §6 의 내용이고, 어기면 조용히 틀린다.

1. **프롬프트 버전을 하드코딩하지 않는다.** 백엔드가 `GET /api/ai/prompt-version` 으로
   읽어가 분석 결과 재사용 키에 넣는다. 프롬프트를 고쳤는데 버전이 그대로면
   낡은 결과가 계속 재사용된다.
2. ~~**`analysisInputHash` 정규화는 Node 구현과 바이트 단위로 같아야 한다.**~~
   → **이 저장소의 일이 아니다.** 해시는 백엔드(`AnalysisInputHasher`, Node 고정값 대조
   테스트 포함)만 계산한다. 같은 로직을 두 언어로 유지하는 것 자체가 어긋날 위험이라
   한쪽으로 몰았다.

   같은 맥락에서 **`_analysisHistoryId` 도 계약에서 빠졌다.** 그 id 는 백엔드 소유 테이블
   `analysis_history` 의 PK 라서 AI 서비스가 만들 방법이 없었다(저장용 엔드포인트도 없었다).
   그대로 두면 어떤 분석 작업도 완료되지 않는다. 적재는 `AnalysisJobRunner` 가 한다.
3. **`aiDisabled` / `aiFallback` 은 성공이 아니다.** 폴백 결과가 캐시에 눌러앉으면
   영원히 재분석되지 않는다.
4. **200 폴백은 백엔드가 만든다. 이 저장소가 아니다.** 예전 문구("LLM 이 실패해도
   분석 엔드포인트는 200 을 준다")는 모놀리스 기준이라 낡았다. AI 서비스는 실패를
   분류된 오류(`503 LLM_UNAVAILABLE` 등)로 올리고, 화면에 보일 200 폴백은 백엔드
   컨트롤러가 `AiUnavailableException` 을 잡아 만든다. 여기서 미리 포장하면 그 폴백이
   `analysis_history` 에 눌러앉아 영원히 재분석되지 않는다(`CLAUDE.md §2` 규칙 1).
