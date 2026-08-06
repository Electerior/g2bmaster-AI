"""텍스트 임베딩 — 원본 `module_server.py` 의 `/api/embed` 를 그대로 옮겼다.

유사도 계산 자체는 백엔드가 한다(MySQL 에 벡터 타입이 없어 코사인을 애플리케이션에서 돈다).
이 서비스는 벡터만 만들어 준다.

무거운 ML 스택(sentence-transformers)은 선택 설치다. 없으면 503 으로 정직하게 알린다 —
임베딩이 없다고 나머지 분석까지 막히면 안 된다.
"""

from __future__ import annotations


class EmbeddingUnavailable(RuntimeError):
    """sentence-transformers / module_a 가 설치되지 않았거나 모델을 못 읽었다."""


def embed_texts(texts: list[str], model: str = "") -> dict:
    if not texts:
        return {"model": "", "dim": 0, "vectors": []}
    try:
        from module_a.title_semantic_searcher import TitleSemanticSearcher
    except ImportError as error:
        raise EmbeddingUnavailable(
            "임베딩 스택이 설치되지 않았습니다. pip install -r requirements-ml.txt 후 다시 시도하세요."
        ) from error

    try:
        searcher = TitleSemanticSearcher()
        vectors = searcher.model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return {
            "model": model or searcher.model_name,
            "dim": int(vectors.shape[1]),
            "vectors": [[float(value) for value in vector] for vector in vectors],
        }
    except Exception as error:  # noqa: BLE001 — 모델 로드 실패도 같은 방식으로 알린다
        raise EmbeddingUnavailable(f"임베딩 생성에 실패했습니다: {error}") from error
