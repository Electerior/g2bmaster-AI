PY ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: venv install install-ml dev start module-server check pool contract errors extractor embedding smoke clean

venv:
	python3 -m venv .venv

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

# 임베딩(/api/embed)과 module_a/module_b 를 쓰려면 추가로 설치한다.
install-ml: install
	$(PIP) install -r requirements-ml.txt

dev:
	AI_RELOAD=1 $(PY) server.py

start:
	$(PY) server.py

# module_a·module_b 서버(기본 8001). 백엔드가 §J 하드웨어 스펙 6개를 여기로 프록시한다.
# INTERNAL_SECRET 이 비면 가드가 모든 요청을 403 으로 막는다 — 반드시 주고 띄운다.
module-server:
	$(PY) module_server.py

check: pool errors contract extractor embedding

pool:
	$(PY) scripts/test_worker_pool.py

# module_b 스키마·근거 좌표·프롬프트 규칙. LLM 없이 돈다.
extractor:
	$(PY) scripts/test_extractor.py

# 임베딩 상주 구조 — 가중치는 공유, 인덱스는 공유 안 함. 모델이 없으면 그 부분만 SKIP.
embedding:
	$(PY) scripts/test_embedding.py

# 실패 한 건의 모양 + docs/failure-modes.md §2 표와 코드의 대조.
errors:
	$(PY) scripts/test_errors.py

# ── bidpipe (조달 공고 마진 분석 파이프라인, 2026-08-25 Electerior 로부터 이전) ──
# 결정론적 코어: LLM 불필요. AGENTS.md 15규칙 + 8스킬 + 가격 DB 를 bidpipe/ 에 둔다.

# 가격 DB 조회로 경로 재배선·데이터 무결성 검증 (367+ 상품)
bidpipe-check:
	BIDPIPE_ROOT=$(CURDIR)/bidpipe $(PY) bidpipe/.agents/scripts/price_lookup.py --help
	@BIDPIPE_ROOT=$(CURDIR)/bidpipe $(PY) -c "import sys; sys.path.insert(0,'bidpipe/.agents/scripts'); import price_schema as ps; print('price DB ok:', len(ps.load_products()), 'products')"

# 기존 산출물 품질 감사 (audit_prices.py) — bidpipe/out/<날짜> 폴더 대상
bidpipe-audit:
	$(PY) bidpipe/.agents/scripts/audit_prices.py $(OUT)

# 워크북 생성 (입력: /tmp/batch*.json) — 산출물은 bidpipe/out/<오늘> 로
bidpipe-gen:
	GEN_OUTDIR=$(CURDIR)/bidpipe/out/$(shell date +%Y%m%d) $(PY) bidpipe/.agents/scripts/gen_analysis.py $(IN)

# HWP 첨부 추출 (SPECDIR + [공고번호...])
bidpipe-extract:
	$(PY) bidpipe/.agents/scripts/extract_specs.py $(SPEC) $(KB)

# 이전 충실성 회귀: 동일 batch JSON을 소스 vs bidpipe 코드로 각각 생성 → 셀+exit 전수 대조.
# 소스 워크스페이스(~/Documents/Elect*)가 없으면 SKIP — 다른 머신에서도 make check 가 깨지지 않는다.
bidpipe-fidelity:
	$(PY) bidpipe/tests/test_migration_fidelity.py

contract:
	$(PY) scripts/test_http_contract.py

# 실제 LLM 서버 연동 확인. 서버가 없으면 SKIP 한다.
smoke:
	$(PY) scripts/smoke_llm.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
