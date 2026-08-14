"""파일 **내용**에 대한 임베딩과 유사도 검색.

제목 의미검색(`title_semantic_searcher`)을 대신한다. 제목은 신호가 너무 얇았다 —
`"2026-09-01호 AI부트캠프 교육과정 운영을 위한 고성능 PC 구매"` 에서 정작 필요한
사양은 한 글자도 없고, 그건 첨부 본문에만 있다.

**인덱스는 상주하지 않는다.** 가중치만 `model_registry` 에서 빌려 쓰고, 청크·벡터는
이 인스턴스 안에서만 산다. 인덱스까지 공유하면 A 요청이 넣은 문서가 B 요청의 검색
결과에 섞여 나오는데, 오류가 나지 않고 결과만 조용히 오염되는 종류라 알아채기 어렵다.
쓰고 버리는 것이 맞다.

Importers:
  - app.embedding: POST /api/embed
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .model_registry import encode, get_model


#: 청크 하나의 목표 길이(문자). 한국어 조달 문서는 표가 많아 줄이 짧다 —
#: 너무 잘게 쪼개면 "24TB 이상" 한 줄만 남아 무엇의 24TB 인지 사라지고,
#: 너무 크게 잡으면 한 벡터에 여러 품목이 뭉개져 유사도가 평평해진다.
DEFAULT_CHUNK_CHARS = 700

#: 청크끼리 겹치는 길이. 경계에 걸린 문장이 어느 쪽에서도 안 읽히는 것을 막는다.
DEFAULT_OVERLAP_CHARS = 120


@dataclass
class Chunk:
    """문서에서 잘라낸 조각과 그 **원문 좌표**.

    좌표를 들고 다니는 이유는 인용 때문이다. 검색 결과를 근거로 쓰려면 원문 어디였는지
    말할 수 있어야 하고, 정규화본의 좌표를 주면 원문 대조가 전부 어긋난다
    (`CLAUDE.md §2-6`).
    """

    text: str
    offset: int
    length: int
    document_id: str = ""

    def as_dict(self) -> dict:
        return {
            "documentId": self.document_id,
            "text": self.text,
            "offset": self.offset,
            "length": self.length,
        }


@dataclass
class Hit:
    """검색 결과 하나."""

    chunk: Chunk
    score: float

    def as_dict(self) -> dict:
        return {**self.chunk.as_dict(), "score": round(float(self.score), 6)}


def split_text(
    text: str,
    document_id: str = "",
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> List[Chunk]:
    """문서를 겹치는 청크로 자른다. 좌표는 **원문 기준**을 유지한다.

    줄 경계를 우선 지킨다. 조달 문서는 한 줄이 한 요구인 경우가 많아, 줄 가운데를
    자르면 `"메모리 96GB"` 와 `"이상"` 이 다른 청크로 갈린다.
    """
    if not text:
        return []
    if chunk_chars <= 0:
        raise ValueError("chunk_chars 는 1 이상이어야 합니다")
    overlap = max(0, min(overlap_chars, chunk_chars - 1))

    chunks: List[Chunk] = []
    start = 0
    length = len(text)

    while start < length:
        end = min(start + chunk_chars, length)
        if end < length:
            # 뒤쪽 1/4 안에서 줄바꿈을 찾아 거기서 끊는다. 없으면 그냥 자른다.
            window = text.rfind("\n", start + (chunk_chars * 3) // 4, end)
            if window > start:
                end = window + 1
        piece = text[start:end]
        if piece.strip():
            chunks.append(Chunk(text=piece, offset=start, length=end - start, document_id=document_id))
        if end >= length:
            break
        start = max(end - overlap, start + 1)

    return chunks


class DocumentIndex:
    """파일 내용에 대한 유사도 검색.

    **쓰고 버린다.** 요청마다 새로 만들어 쓰는 것을 전제로 한다 — 무거운 것은
    가중치이고 그건 프로세스가 공유하므로, 인스턴스를 새로 만드는 비용은 거의 없다.
    """

    def __init__(self, model_name: Optional[str] = None) -> None:
        self.model_name = model_name
        self.chunks: List[Chunk] = []
        self._vectors: Optional[np.ndarray] = None

    def __len__(self) -> int:
        return len(self.chunks)

    @property
    def dim(self) -> int:
        return 0 if self._vectors is None else int(self._vectors.shape[1])

    def add_document(self, text: str, document_id: str = "", **split_kwargs) -> int:
        """문서 하나를 잘라 넣는다. 넣은 청크 수를 돌려준다."""
        return self.add_chunks(split_text(text, document_id, **split_kwargs))

    def add_chunks(self, chunks: Sequence[Chunk]) -> int:
        usable = [c for c in chunks if c.text.strip()]
        if not usable:
            return 0

        vectors = encode([c.text for c in usable], model=self.model_name)
        self.chunks.extend(usable)
        self._vectors = vectors if self._vectors is None else np.vstack([self._vectors, vectors])
        return len(usable)

    def search(self, query: str, top_k: int = 5, min_score: float = 0.0) -> List[Hit]:
        """질의와 가장 가까운 청크를 돌려준다.

        벡터가 정규화돼 있으므로 내적이 곧 코사인 유사도다.
        """
        if not query.strip() or self._vectors is None or not self.chunks:
            return []

        query_vector = encode([query], model=self.model_name)[0]
        scores = self._vectors @ query_vector

        top_k = max(1, min(top_k, len(self.chunks)))
        # 전체를 정렬하지 않는다 — 청크가 수만 개가 되면 정렬이 검색보다 비싸진다.
        picked = np.argpartition(-scores, top_k - 1)[:top_k]
        picked = picked[np.argsort(-scores[picked])]

        return [
            Hit(chunk=self.chunks[i], score=float(scores[i]))
            for i in picked
            if float(scores[i]) >= min_score
        ]


def similarity(left: str, right: str, model_name: Optional[str] = None) -> float:
    """문장 두 개의 코사인 유사도. 인덱스를 만들 것도 없는 단발 비교용."""
    if not left.strip() or not right.strip():
        return 0.0
    vectors = encode([left, right], model=model_name)
    return float(vectors[0] @ vectors[1])
