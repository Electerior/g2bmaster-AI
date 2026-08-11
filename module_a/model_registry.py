"""임베딩 모델을 프로세스에 **상주**시킨다.

**상주하는 것은 가중치뿐이다.** 이 경계가 이 파일의 핵심이다.

| 상주 | 상주하지 않음 |
|---|---|
| 모델 가중치·토크나이저 (읽기 전용) | `DocumentIndex.chunks` |
| | `DocumentIndex._vectors` |

가중치는 아무리 공유해도 호출끼리 섞일 것이 없다 — 같은 입력에 같은 출력을 내는
순수 함수다. 반면 인덱스는 **누가 무엇을 넣었는지가 결과를 바꾼다.** 인덱스까지
상주시키면 A 요청이 넣은 문서가 B 요청의 검색 결과에 섞여 나오는데, 오류가 나지 않고
결과만 조용히 달라지는 종류라 알아채기 어렵다. 그래서 인덱스는 쓰고 버리고,
무거운 가중치만 여기서 공유한다.

지금까지는 상주하지 않았다. `app/embedding.py` 가 요청마다 검색기를 새로 만들었고,
그 인스턴스가 자기 안에서만 모델을 캐시했다. 결과는 실측으로
**호출당 약 4초** — 벡터를 만드는 시간이 아니라 모델을 디스크에서 다시 읽는 시간이다.
`/api/embed` 를 조금만 자주 부르면 그 4초가 매번 다시 붙는다.

여기서 프로세스 수명 동안 한 번만 읽고 공유한다. 상주 대상은 순수한 읽기 전용
가중치라 `CLAUDE.md §2-10`("내구 상태를 갖지 않는다")에 어긋나지 않는다 — 프로세스가
죽으면 다시 읽으면 그만이고, 잃는 것이 없다.

Importers:
  - module_a.document_index: 파일 내용 청크 임베딩·유사도 검색
  - app.embedding: POST /api/embed
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Sequence

log = logging.getLogger(__name__)

#: 기본 모델. **다국어판이어야 한다.**
#:
#: 이름이 비슷한 `paraphrase-MiniLM-L12-v2` 는 **영어 전용**이라 한국어 문서에서
#: 점수가 거의 무작위가 된다. 우리가 다루는 규격서·공고문은 전부 한국어이므로 그
#: 차이가 곧 검색 품질이다. 바꾸려면 한국어 표본으로 재 보고 바꾼다.
DEFAULT_MODEL = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

#: 모델을 못 읽었을 때 호출자에게 보이는 예외.
class ModelUnavailable(RuntimeError):
    """가중치를 읽지 못했다 — 스택 미설치이거나 캐시에 없고 내려받지도 못했다."""


_lock = threading.Lock()
_models: Dict[str, Any] = {}
_meta: Dict[str, Dict[str, Any]] = {}
_failures: Dict[str, str] = {}


def get_model(name: Optional[str] = None):
    """상주 모델을 돌려준다. 없으면 한 번만 읽는다.

    uvicorn 은 동기 엔드포인트를 스레드풀에서 돌리므로 동시에 들어올 수 있다.
    잠금 없이 두면 같은 모델을 여러 스레드가 동시에 읽어 메모리를 몇 배로 쓴다.
    """
    key = name or DEFAULT_MODEL

    cached = _models.get(key)
    if cached is not None:
        return cached

    with _lock:
        # 잠금을 기다리는 사이에 다른 스레드가 이미 읽어 놨을 수 있다.
        cached = _models.get(key)
        if cached is not None:
            return cached

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            _failures[key] = "sentence-transformers 미설치"
            raise ModelUnavailable(
                "임베딩 스택이 설치되지 않았습니다. make install-ml 후 다시 시도하세요."
            ) from error

        started = time.monotonic()
        try:
            model = SentenceTransformer(key)
        except Exception as error:  # noqa: BLE001 — 원인은 문구로 넘긴다
            _failures[key] = str(error)
            raise ModelUnavailable(f"모델 '{key}' 을(를) 읽지 못했습니다: {error}") from error

        # 추론 전용으로 못박는다. 학습 모드로 남아 있으면 dropout 이 살아 있어
        # **같은 문장이 호출마다 다른 벡터**를 낸다 — 상주 상태에서 가장 알아채기
        # 어려운 오염이다(오류가 안 나고 점수만 흔들린다).
        try:
            model.eval()
        except Exception:  # noqa: BLE001 — eval 이 없는 구현이면 그대로 쓴다
            pass

        elapsed = time.monotonic() - started
        _models[key] = model
        _meta[key] = {
            "model": key,
            "dim": _dimension(model),
            "device": str(getattr(model, "device", "")),
            "loadSeconds": round(elapsed, 2),
        }
        _failures.pop(key, None)
        log.info("임베딩 모델 상주 시작: %s (%.2fs, dim=%s)", key, elapsed, _meta[key]["dim"])
        return model


def encode(texts: Sequence[str], model: Optional[str] = None, normalize: bool = True):
    """문장들을 벡터로 만든다. **상주 모델을 만지는 유일한 통로다.**

    호출자에게 모델 객체를 넘기지 않고 이 함수만 쓰게 하는 이유는 오염 때문이다.
    `model.max_seq_length` 같은 속성은 인스턴스에 붙어 있어서, 한 호출자가 바꾸면
    그 뒤의 **모든** 호출자가 바뀐 값으로 인코딩한다. 상주시키는 순간 그런 변경은
    프로세스 전체에 남는다.

    `inference_mode` 로 감싸 자동미분 그래프가 쌓이지 않게 한다. 없으면 상주 프로세스의
    메모리가 호출할수록 늘어난다 — 느리게 새는 종류라 한참 뒤에야 드러난다.
    """
    resident = get_model(model)
    if not texts:
        import numpy as np

        return np.zeros((0, _meta.get(model or DEFAULT_MODEL, {}).get("dim", 0)))

    try:
        import torch

        with torch.inference_mode():
            return resident.encode(
                list(texts), show_progress_bar=False, normalize_embeddings=normalize
            )
    except ImportError:
        # torch 가 없으면 sentence-transformers 도 못 뜬다. 여기까지 왔다는 건
        # 다른 백엔드라는 뜻이므로 그대로 부른다.
        return resident.encode(list(texts), show_progress_bar=False, normalize_embeddings=normalize)


def warmup(name: Optional[str] = None) -> Dict[str, Any]:
    """모델을 읽고 한 번 돌려 본다.

    가중치를 읽는 것만으로는 첫 호출이 빨라지지 않는다 — 실제로 인코딩할 때
    지연 초기화되는 것들이 남아 있다. 그래서 짧은 문장 하나를 실제로 넣어 본다.

    **예외를 던지지 않는다.** 기동 중에 부르는 자리라, 모델이 없다고 서비스 전체가
    못 뜨면 안 된다. 임베딩만 못 쓰는 상태로 뜨고 나머지는 정상으로 돈다.
    """
    key = name or DEFAULT_MODEL
    try:
        model = get_model(key)
        model.encode(["예열"], show_progress_bar=False, normalize_embeddings=True)
    except ModelUnavailable as error:
        log.warning("임베딩 모델 예열 실패: %s", error)
        return {"model": key, "ready": False, "error": str(error)}
    except Exception as error:  # noqa: BLE001
        _failures[key] = str(error)
        log.warning("임베딩 모델 예열 중 오류: %s", error)
        return {"model": key, "ready": False, "error": str(error)}

    return {**_meta.get(key, {"model": key}), "ready": True}


def warmup_async(name: Optional[str] = None) -> threading.Thread:
    """예열을 백그라운드로 돌린다.

    모델을 읽는 데 몇 초가 걸린다. 기동 경로에서 동기로 기다리면 그동안 헬스체크가
    응답하지 않아, 오케스트레이터 눈에는 "안 뜨는 서비스"로 보인다.
    """
    thread = threading.Thread(target=warmup, args=(name,), name="embedding-warmup", daemon=True)
    thread.start()
    return thread


def status() -> Dict[str, Any]:
    """지금 무엇이 상주해 있는가. 헬스 표면이 그대로 실어 보낼 수 있는 모양이다."""
    return {
        "default": DEFAULT_MODEL,
        "loaded": [dict(meta) for meta in _meta.values()],
        "failures": dict(_failures),
        "ready": DEFAULT_MODEL in _models,
    }


def reset() -> None:
    """상주분을 버린다. 시험에서 재적재를 확인할 때만 쓴다."""
    with _lock:
        _models.clear()
        _meta.clear()
        _failures.clear()


def _dimension(model: Any) -> int:
    # sentence-transformers 5.x 에서 이름이 바뀌었다. 둘 다 받아 둔다 —
    # 차원을 못 읽는다고 모델을 버릴 이유는 없고, 경고만 쌓여도 로그가 흐려진다.
    for attribute in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        getter = getattr(model, attribute, None)
        if getter is None:
            continue
        try:
            return int(getter())
        except Exception:  # noqa: BLE001
            continue
    return 0
