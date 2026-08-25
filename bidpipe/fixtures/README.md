# 규격해석 품질 fixture

파이프라인의 **"AI 몫"(상품 발굴·규격 대조·예외 해석)의 품질**을 고정 케이스로 회귀한다.

`test_migration_fidelity.py`(이전 충실성)가 **결정론적 코드**가 같은 출력을 내는지 보는 것이라면,
여기는 **LLM**이 공고 규격을 해석했을 때 **최종 워크북의 정답과 일치**하는지 보는 것이다.
URL·추정가 게이트는 모델 무관하게 `gen_analysis.py`가 막지만, 규격 오탐
(DDR5-5600을 5200으로, Server/Workstation 구분을 놓치기)은 모델의 이해력 문제라
단독 안전장치가 필요했다 — 이게 그 장치.

## 케이스 (3건)

| 디렉터리 | 공고 | 검증 포인트 |
|---|---|---|
| `kisti_9100pro_8tb` | KISTI 삼성 9100 PRO 8TB ×2 | 단일 품목 + **정식 유통** 추가요구사항 판독 |
| `kitech_ws_9950x` | 생산기술원 워크스테이션 (재입찰) | 8부품 + **스펙 미기재 파워를 조항 인용으로 발굴** (예외 해석) + workstation edition |
| `jh_gpu_server` | JH솔루션 GPU 서버 (긴급) | 8부품 + **Server Edition(Passive) vs Workstation 구분** + PSU 1+1 이중화 |

각 케이스:
- `spec_excerpt.txt` — 규격서 원문 중 핵심 구절 (**LLM 입력**, 가격·시세 정보 불포함)
- `expected.json` — 정답 (최종 워크북 물품행에서 추출)
- `meta.json` — 공고 메타

## 정답(expected.json) 필드

- `required_items` — 반드시 sel=O 로 나와야 할 품목. `name_kws`(all-of, 스펙 용어만) + `qty_min`.
  순수 동의어 목록이면 `any_of:true`. **정답 워크북의 브랜드명(CORSAIR FRAME 같은)을 name_kws에
  넣으면 LLM이 재현할 수 없는 요구가 되므로 금지.**
- `forbidden` — sel=O 행에 나타나면 안 되는 것(모델 불일치·규격미달). `name_kws` all-of.
- `must_mention` — 전체 응답 어디엔가 있어야 할 판정 근거(동의어 any-of). 없으면 **FAIL**.
- `should_mention` — 있으면 좋은 것. 없으면 **WARN**만.

## 실행

```
make bidpipe-fixture                # 전체
make bidpipe-fixture CASE=jh_gpu_server
```

- LLM 미도달이면 SKIP(exit 0), 케이스 FAIL이면 exit 1.
- 응답 덤프는 `<케이스>/last_response.json` (gitignore 대상). FAIL 원인 분석용.

## 판정 기준

- **PASS**: required 전부 + forbidden 무위반 + must_mention 전부. should_mention 누락은 경고.
- **FAIL**: required 1건 누락, forbidden 1건 위반, must_mention 1건 누락 중 하나.

## 설계 의도 — 왜 이런 매처인가

첫 구현에서 `name_kws`·`must_mention`을 **all-of**으로 했는데, LLM은 실제로 잘 해석한 것을
"정답의 브랜드명까지 재현 못 해서" 오FAIL 시켰다 (JH: "서버 섀시"를 잘 썼는데 베어본+ESC8000을
요구, "3년 무상보증"은 "24개월"이 없으니까 탈락). 교훈:

1. **매처는 LLM이 재현할 수 있는 것만 요구** — 스펣에 나오는 용어(all-of), 또는 동의어(any-of).
2. **가격·시세 지식은 이 fixture의 범위가 아니다.** CPU 6507P가 "견적 필요"인지는 가격 DB
   (국내 실판매가 유무)를 알아야 하는 **가격 단계**의 문제고, 그건 `price_lookup.py` DB +
   결정론적 게이트 + `test_migration_fidelity.py`가 커버한다. 규격 해석만 본다.
3. **부정테스트로 판별력 검증** — 의도적으로 틀린 응답(파워 누락·DDR4 선택·4TB 용량오류·
   Workstation O선택)을 넣으면 전부 FAIL해야 한다. 넣지 않으면 이 장치는 통과만 하는 장식이 된다.

## 케이스 추가

1. `bidpipe/fixtures/<케이스>/` 에 `spec_excerpt.txt`(규격 핵심 구절)·`meta.json`·`expected.json` 생성.
2. `expected.json`은 **최종 워크북의 물품행**(sel=O)에서 추출 — 그 워크북이 이미 검증된 정답이다.
3. `make bidpipe-fixture` 로 실행, 그리고 **부정테스트**로 나쁜 답이 FAIL 되는지 확인.
