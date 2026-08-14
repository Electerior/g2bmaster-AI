"""Module A: 파일 **내용**에 대한 임베딩과 유사도 검색.

제목 의미검색은 폐지했다(`TitleSemanticSearcher`·`data_loader`). 공고 제목에는 정작
필요한 사양이 한 글자도 없고, 그건 첨부 본문에만 있다 — 제목으로 재던 유사도는
신호가 너무 얇았다.

구성:
  - `model_registry` — 가중치를 프로세스에 상주시킨다. **여기만 공유된다.**
  - `document_index` — 문서를 청크로 잘라 벡터를 만들고 검색한다. **쓰고 버린다.**

이 분리가 핵심이다. 가중치는 순수 함수라 공유해도 섞일 것이 없지만, 인덱스는
누가 무엇을 넣었는지가 결과를 바꾼다. 인덱스를 상주시키면 다른 요청이 넣은 문서가
검색 결과에 섞여 나오고, 오류 없이 결과만 달라지므로 알아채기 어렵다.
"""

from __future__ import annotations

from .document_index import Chunk, DocumentIndex, Hit, similarity, split_text
from .model_registry import (
    DEFAULT_MODEL,
    ModelUnavailable,
    encode,
    get_model,
    status,
    warmup,
    warmup_async,
)

__all__ = [
    "Chunk",
    "DocumentIndex",
    "Hit",
    "similarity",
    "split_text",
    "DEFAULT_MODEL",
    "ModelUnavailable",
    "encode",
    "get_model",
    "status",
    "warmup",
    "warmup_async",
]
