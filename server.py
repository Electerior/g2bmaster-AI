"""실행 진입점.

    python server.py                         # 기본 127.0.0.1:8000
    uvicorn app.main:app --port 8000

백엔드의 AI_BASE_URL 기본값이 http://localhost:8000 이라 포트를 8000 으로 맞춘다.
"""

from __future__ import annotations

import os

import uvicorn

from app.config import HOST, PORT

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=bool(os.getenv("AI_RELOAD")))
