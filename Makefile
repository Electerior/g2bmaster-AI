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

contract:
	$(PY) scripts/test_http_contract.py

# 실제 LLM 서버 연동 확인. 서버가 없으면 SKIP 한다.
smoke:
	$(PY) scripts/smoke_llm.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
