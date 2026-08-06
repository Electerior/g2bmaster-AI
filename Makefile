PY ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: venv install install-ml dev start check pool contract errors smoke clean

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

check: pool errors contract

pool:
	$(PY) scripts/test_worker_pool.py

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
