"""Module A: Title Semantic Searcher.

Core class for ultra-fast semantic search over hardware titles using
sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2).

Hybrid retrieval: dense embeddings (meaning) fused with BM25 (exact tokens)
via Reciprocal Rank Fusion. Dense alone misses model numbers and part codes --
"RTX 4090" and "i9-13900K" are near-meaningless to an embedding model but are
exactly what BM25 is good at. Korean procurement titles are full of them.

The model MUST stay multilingual. all-MiniLM-* is English-only and scores
Korean titles near-randomly; scripts/quality_guard.js enforces this.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Tuple, Optional

import numpy as np
from sentence_transformers import SentenceTransformer

try:                                    # BM25 가 없으면 dense 단독으로 물러선다
    from rank_bm25 import BM25Okapi
except ImportError:                     # pragma: no cover - 선택 의존성
    BM25Okapi = None


class TitleSemanticSearcher:
    """Semantic title searcher using MiniLM embeddings.

    Attributes:
        model_name: The sentence-transformers model to use.
        embeddings: Numpy array of shape (n_titles, embedding_dim).
        titles: List of title strings corresponding to embeddings.
    """

    _DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        cache_dir: Optional[Path] = None,
    ) -> None:
        """Initialize the searcher with a sentence-transformers model.

        Args:
            model_name: HuggingFace model identifier for sentence-transformers.
            cache_dir: Optional directory to cache embeddings/index.
        """
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model: Optional[SentenceTransformer] = None
        self.embeddings: Optional[np.ndarray] = None
        self.titles: List[str] = []
        self._bm25 = None                       # 제목이 바뀔 때마다 재구축

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """BM25 용 토크나이저 — 한글/영숫자만 남기고 공백 분할.

        모델명·규격코드가 살아남는 게 목적이라 1글자 토큰만 버린다
        (예: 'RTX 4090' -> ['rtx', '4090']).
        """
        cleaned = re.sub(r"[^\w가-힣\s]", " ", text.lower())
        return [t for t in cleaned.split() if len(t) > 1]

    def _rebuild_sparse(self) -> None:
        """제목 목록에서 BM25 인덱스를 다시 만든다."""
        if BM25Okapi is None or not self.titles:
            self._bm25 = None
            return
        self._bm25 = BM25Okapi([self._tokenize(t) for t in self.titles])

    @property
    def model(self) -> SentenceTransformer:
        """Lazy-load the sentence transformer model."""
        if self._model is None:
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def add_titles(self, titles: List[str]) -> None:
        """Add new titles to the search index incrementally.

        Args:
            titles: List of title strings to add.
        """
        new_titles = [t.strip() for t in titles if t and t.strip()]
        if not new_titles:
            return

        # Embed new titles
        new_embeddings = self.model.encode(new_titles, show_progress_bar=False, normalize_embeddings=True)

        if self.embeddings is None or self.embeddings.size == 0:
            self.embeddings = new_embeddings
        else:
            self.embeddings = np.vstack([self.embeddings, new_embeddings])

        self.titles.extend(new_titles)
        self._rebuild_sparse()

    def build_index(self, titles: List[str]) -> None:
        """Build the full search index from a list of titles.

        Replaces any existing index.

        Args:
            titles: List of title strings to index.
        """
        self.titles = [t.strip() for t in titles if t and t.strip()]
        if not self.titles:
            self.embeddings = None
            self._bm25 = None
            return

        self.embeddings = self.model.encode(
            self.titles,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        self._rebuild_sparse()

    @staticmethod
    def _normalize(x: np.ndarray) -> np.ndarray:
        """최대값 1 로 맞춘다. 전부 0이면 그대로 둔다(0으로 나누지 않는다)."""
        peak = x.max() if x.size else 0.0
        return x / peak if peak > 0 else x

    @classmethod
    def _fuse(cls, dense: np.ndarray, sparse: np.ndarray, sparse_weight: float) -> np.ndarray:
        """정규화 점수 융합.

        처음에는 RRF(Reciprocal Rank Fusion)를 썼는데 실측에서 뒤집혔다.
        2,824건 GPU 데이터로 'RTX 4090' 을 질의하면 BM25 단독은 정답을 1위로
        내는데, RRF 로 융합하면 오답('RTX A400')이 1위가 됐다. 이유는 순위 융합이
        **BM25 점수의 크기를 버리기** 때문이다. '질의어 전부 일치'와 '한 단어만
        일치'가 순위상 몇 칸 차이로 뭉개지는 사이, dense 의 1위 확신은 그대로
        살아남는다. 가중치를 5배까지 올려도 뒤집히지 않았다.

        그래서 순위 대신 정규화한 점수를 더한다. 전체 토큰이 맞은 문서는 BM25
        점수 자체가 압도적이라 그 신뢰도가 융합 후에도 남는다.

        sparse_weight 는 계산이 아니라 조율 값이다. 0.5 는 식별자 질의(모델명·
        규격코드)를 전부 바로잡으면서 한국어 개념 질의 결과를 dense 와 동일하게
        유지하는 지점으로 실측해 골랐다. 데이터가 바뀌면 다시 재야 한다.
        """
        return cls._normalize(np.clip(dense, 0, None)) + sparse_weight * cls._normalize(sparse)

    _SPARSE_WEIGHT = 0.5      # 조율 값 — _fuse 주석 참조

    def search(
        self,
        query: str,
        top_k: int = 5,
        mode: str = "hybrid",
        sparse_weight: Optional[float] = None,
    ) -> List[Tuple[str, float]]:
        """Search for titles similar to the query.

        Args:
            query: Search query string.
            top_k: Number of top results to return.
            mode: "hybrid" (dense + BM25, default), "dense", or "sparse".

        Returns:
            List of (title, cosine_similarity) tuples, best first.

            점수는 순위와 무관하게 **항상 코사인 유사도**다. 순위는 hybrid 일 때
            RRF 가 정하지만, RRF 값(약 0.03)은 "얼마나 비슷한가"를 뜻하지 않아
            호출부가 임계값으로 쓸 수 없기 때문이다.
        """
        if self.embeddings is None or len(self.titles) == 0:
            return []

        query_embedding = self.model.encode([query], normalize_embeddings=True)
        # 임베딩이 정규화돼 있어 내적 = 코사인 유사도
        similarities = np.dot(self.embeddings, query_embedding.T).flatten()

        use_sparse = mode in ("hybrid", "sparse") and self._bm25 is not None
        if use_sparse:
            sparse = np.asarray(self._bm25.get_scores(self._tokenize(query)), dtype=float)
            weight = self._SPARSE_WEIGHT if sparse_weight is None else sparse_weight
            ranking = sparse if mode == "sparse" else self._fuse(similarities, sparse, weight)
        else:
            ranking = similarities

        top_k = min(top_k, len(self.titles))
        top_indices = np.argpartition(ranking, -top_k)[-top_k:]
        top_indices = top_indices[np.argsort(ranking[top_indices])[::-1]]

        return [(self.titles[i], float(similarities[i])) for i in top_indices]

    def save_index(self, path: Path) -> None:
        """Save the index (embeddings + titles) to disk.

        Uses NumPy's native format (.npz) for embeddings (safe, no pickle)
        and JSON for titles.

        Args:
            path: File path to save index (will save .npz and .json).
        """
        if self.embeddings is None or not self.titles:
            raise ValueError("No index to save")

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Save embeddings using NumPy's safe format
        np.savez_compressed(
            path.with_suffix(".npz"),
            embeddings=self.embeddings,
        )

        # Save metadata + titles as JSON
        metadata = {
            "model_name": self.model_name,
            "num_titles": len(self.titles),
        }
        with open(path.with_suffix(".json"), "w", encoding="utf-8") as f:
            json.dump({"metadata": metadata, "titles": self.titles}, f, ensure_ascii=False, indent=2)

    @classmethod
    def load_index(cls, path: Path, cache_dir: Optional[Path] = None) -> "TitleSemanticSearcher":
        """Load a saved index from disk.

        Args:
            path: Path to the .json file (loads .npz alongside it).
            cache_dir: Optional cache directory for the model.

        Returns:
            A new TitleSemanticSearcher instance with loaded index.
        """
        path = Path(path)
        json_path = path.with_suffix(".json")
        npz_path = path.with_suffix(".npz")

        if not json_path.exists() or not npz_path.exists():
            raise FileNotFoundError(f"Index files not found: {json_path} or {npz_path}")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        metadata = data.get("metadata", {})
        titles = data.get("titles", [])

        npz_data = np.load(npz_path)
        embeddings = npz_data["embeddings"]

        searcher = cls(model_name=metadata.get("model_name", cls._DEFAULT_MODEL), cache_dir=cache_dir)
        searcher.embeddings = embeddings
        searcher.titles = titles
        # BM25 는 저장하지 않는다 — 제목만 있으면 즉시 재구축되고(임베딩과 달리 모델 불필요),
        # 직렬화하면 pickle 이 필요해져 저장 형식의 안전성이 깨진다.
        searcher._rebuild_sparse()
        return searcher


def create_searcher_from_csv(
    csv_path: Path,
    title_column: str = "name",
    cache_dir: Optional[Path] = None,
) -> TitleSemanticSearcher:
    """Convenience function to create a searcher from a CSV file.

    Args:
        csv_path: Path to CSV file containing titles.
        title_column: Name of the column containing titles.
        cache_dir: Optional cache directory.

    Returns:
        Configured TitleSemanticSearcher with built index.
    """
    import csv

    titles = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row.get(title_column, "").strip()
            if title:
                titles.append(title)

    searcher = TitleSemanticSearcher(cache_dir=cache_dir)
    searcher.build_index(titles)
    return searcher