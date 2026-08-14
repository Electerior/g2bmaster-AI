"""텍스트 임베딩 — 원본 `module_server.py` 의 `/api/embed` 를 그대로 옮겼다.

유사도 계산 자체는 백엔드가 한다(MySQL 에 벡터 타입이 없어 코사인을 애플리케이션에서 돈다).
이 서비스는 벡터만 만들어 준다.

무거운 ML 스택(sentence-transformers)은 선택 설치다. 없으면 503 으로 정직하게 알린다 —
임베딩이 없다고 나머지 분석까지 막히면 안 된다.

**모델은 프로세스에 상주한다**(`module_a.model_registry`). 예전에는 호출마다 검색기를
새로 만들어 가중치를 다시 읽었고, 그것만으로 호출당 약 4초가 붙었다(실측).
상주하는 것은 읽기 전용 가중치뿐이고 인덱스 같은 가변 상태는 공유하지 않는다.
"""

from __future__ import annotations


class EmbeddingUnavailable(RuntimeError):
    """sentence-transformers / module_a 가 설치되지 않았거나 모델을 못 읽었다."""


def embed_texts(texts: list[str], model: str = "") -> dict:
    if not texts:
        return {"model": "", "dim": 0, "vectors": []}
    try:
        from module_a import model_registry
    except ImportError as error:
        raise EmbeddingUnavailable(
            "임베딩 스택이 설치되지 않았습니다. pip install -r requirements-ml.txt 후 다시 시도하세요."
        ) from error

    try:
        vectors = model_registry.encode(texts, model=model or None)
        return {
            "model": model or model_registry.DEFAULT_MODEL,
            "dim": int(vectors.shape[1]),
            "vectors": [[float(value) for value in vector] for vector in vectors],
        }
    except model_registry.ModelUnavailable as error:
        raise EmbeddingUnavailable(str(error)) from error
    except Exception as error:  # noqa: BLE001 — 인코딩 실패도 같은 방식으로 알린다
        raise EmbeddingUnavailable(f"임베딩 생성에 실패했습니다: {error}") from error


def warmup() -> dict:
    """가중치를 미리 읽어 둔다. 기동 경로에서 부른다.

    예외를 던지지 않는다 — 임베딩을 못 써도 나머지 표면 10개는 정상으로 떠야 한다.
    """
    try:
        from module_a import model_registry
    except ImportError:
        return {"ready": False, "error": "임베딩 스택 미설치"}
    return model_registry.warmup()


def status() -> dict:
    """무엇이 상주해 있는가. 헬스 표면이 그대로 실어 보낼 수 있다."""
    try:
        from module_a import model_registry
    except ImportError:
        return {"ready": False, "loaded": [], "error": "임베딩 스택 미설치"}
    return model_registry.status()
