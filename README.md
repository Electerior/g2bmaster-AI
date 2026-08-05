# g2bmaster-AI

나라장터(G2B) 입찰정보의 **추론 계층** — LLM 분석·임베딩·법령 검토·가격 웹검색.

모놀리스 [`g2bmastersopen`](https://github.com/Electerior/g2bmastersopen) 를 세 저장소로
나눈 것 중 하나다.

| 저장소 | 역할 | 스택 |
|---|---|---|
| [`g2bmaster-frontend`](https://github.com/Electerior/g2bmaster-frontend) | 화면 | React 18 + TypeScript + Vite |
| [`g2bmaster-backend`](https://github.com/Electerior/g2bmaster-backend) | API·적재·영속 | Spring Boot 4.1 + MySQL 8 |
| **`g2bmaster-AI`** (이 저장소) | 추론 | 원본의 Node LLM 모듈 + Python 모듈 서버 |

---

## 실행

**이 저장소는 아직 비어 있다. 지금은 띄울 것이 없다.**
그리고 그래도 된다 — 백엔드를 `AI_ENABLED=false` 로 두면 검색·트렌드·저장 공고·
운영 화면은 전부 정상 동작한다.

```bash
cd ../g2bmaster-backend && AI_ENABLED=false ./mvnw spring-boot:run
```

AI 없이 되는 것과 안 되는 것의 정확한 목록은 `g2bmaster-backend/docs/ai-boundary.md` §7 에 있다.

### 구현이 들어온 뒤

백엔드가 요구하는 것은 하나뿐이다 — **`AI_BASE_URL`(기본 `http://localhost:8000`)에서
HTTP 로 응답할 것.** 언어도 프레임워크도 백엔드는 모른다.

원본의 Python 모듈 서버와 같은 방식으로 띄운다면:

```bash
python -m pip install -r requirements.txt
```

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

그리고 백엔드는 주소만 맞춰 주면 된다.

```bash
cd ../g2bmaster-backend && AI_ENABLED=true AI_BASE_URL=http://localhost:8000 ./mvnw spring-boot:run
```

---

## 무엇을 만들어야 하나

계약 전문은 `g2bmaster-backend/docs/ai-boundary.md` 에 있다.
`integration/ai/AiClient` 가 부르는 엔드포인트는 11개다.

| 메서드 | 경로 | 용도 |
|---|---|---|
| POST | `/api/item-summary` | 공고·사전규격·발주계획 심층 분석 (작업 큐가 소비) |
| POST | `/api/bid-summary` | 공고 영업 요약 |
| POST | `/api/legal/review-clauses` | 조항 위법성 검토 |
| POST | `/api/legal/outreach-draft` | 콜드메일 초안 |
| POST | `/api/pledge/revision-workflow` | 서약서 수정본 생성 |
| POST | `/api/price/resolve` | 품목명 → 웹 가격 |
| POST | `/api/price/url` | URL 지정 가격 조회 |
| POST | `/api/embed` | 텍스트 임베딩 (유사도 계산은 백엔드가 한다) |
| GET | `/api/ai/prompt-version` | 분석 재사용 키에 들어가는 프롬프트 버전 |
| GET | `/api/ai/capacity` | 워커 용량 (내보내기 ETA 계산용) |
| GET | `/api/llm/models` | 모델 목록·도달 여부 (시스템 화면) |

원본에서 이쪽으로 넘어오는 모듈: `lib/lms.js`, `lib/llm-worker-pool.js`,
`lib/law-mcp.js`, `lib/legal-review.js`, `lib/pledge-workflow.js`,
`lib/pledge-revision.js`, `lib/ai-config.js`, `price-web.js`,
그리고 `module_a/` · `module_b/` · `korean-law-mcp/` 일체.

### 먼저 확인할 네 가지

`ai-boundary.md` §6 의 내용이고, 어기면 조용히 틀린다.

1. **프롬프트 버전을 하드코딩하지 않는다.** 백엔드가 `GET /api/ai/prompt-version` 으로
   읽어가 분석 결과 재사용 키에 넣는다. 프롬프트를 고쳤는데 버전이 그대로면
   낡은 결과가 계속 재사용된다.
2. **`analysisInputHash` 정규화는 Node 구현과 바이트 단위로 같아야 한다.**
   어긋나면 이관 순간 기존 분석 캐시가 통째로 고아가 된다.
3. **`aiDisabled` / `aiFallback` 은 성공이 아니다.** 폴백 결과가 캐시에 눌러앉으면
   영원히 재분석되지 않는다.
4. **LLM 이 실패해도 분석 엔드포인트는 200 을 준다.** 첨부에서 뽑은 문서 태그·규격
   원문은 LLM 과 무관하게 유효하고, 사용자는 그것만으로도 판단을 이어갈 수 있다.
