#!/usr/bin/env python3
"""임베딩 상주 구조 — 가중치는 공유하고 인덱스는 공유하지 않는다.

여기서 지키는 선이 하나 있다. **상주하는 것은 읽기 전용 가중치뿐이다.**
인덱스까지 상주시키면 A 요청이 넣은 문서가 B 요청의 검색 결과에 섞이는데,
예외가 나지 않고 결과만 조용히 달라지므로 운영에서 알아채기 어렵다.

모델이 설치돼 있지 않으면 그 부분은 SKIP 한다 — ML 스택은 선택 설치다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from module_a import model_registry  # noqa: E402
from module_a.document_index import DocumentIndex, split_text  # noqa: E402

FAILURES: list[str] = []
SKIPPED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(name)


def skip(name: str, why: str) -> None:
    global SKIPPED
    SKIPPED += 1
    print(f"  skip {name} — {why}")


# ── 청크 자르기 (모델 없이 돈다) ──────────────────────────────────────────────
print("청크 자르기")

DOC = "\n".join([
    "1. 물품 규격",
    "  가. GPU: 메모리 96GB 이상, CUDA 코어 24064개 이상",
    "  나. 저장장치: 3.5형 SATA 24TB 이상 4개",
    "  다. 전원: 3000W 80PLUS Titanium 이중화",
] + [f"  참고 {i}. 납품 조건 및 검수 절차에 관한 사항" for i in range(40)])

chunks = split_text(DOC, document_id="spec-1", chunk_chars=300, overlap_chars=60)
check("여러 청크로 갈린다", len(chunks) > 1, f"got={len(chunks)}")
check("문서 id 가 붙는다", all(c.document_id == "spec-1" for c in chunks))
check(
    "좌표가 원문을 정확히 가리킨다",
    all(DOC[c.offset : c.offset + c.length] == c.text for c in chunks),
)
check("첫 청크는 문서 처음부터다", chunks[0].offset == 0)
check(
    "청크가 겹친다 — 경계에 걸린 줄이 사라지지 않는다",
    any(chunks[i + 1].offset < chunks[i].offset + chunks[i].length for i in range(len(chunks) - 1)),
)
check("빈 문서는 빈 목록", split_text("") == [])
check("공백만 있는 청크는 버린다", all(c.text.strip() for c in chunks))

joined = "".join(
    DOC[c.offset : c.offset + c.length] for c in split_text(DOC, chunk_chars=300, overlap_chars=0)
)
check("겹침이 없으면 원문을 그대로 덮는다", joined.replace("\n", "") == DOC.replace("\n", ""))


# ── 상주 구조 ────────────────────────────────────────────────────────────────
print("\n상주 구조")

try:
    model_registry.get_model()
    have_model = True
except model_registry.ModelUnavailable as error:
    have_model = False
    REASON = str(error)[:60]

if not have_model:
    for name in ["가중치는 한 번만 읽는다", "인덱스는 공유되지 않는다", "유사도가 뜻을 잡는다"]:
        skip(name, REASON)
else:
    first = model_registry.get_model()
    second = model_registry.get_model()
    check("가중치는 한 번만 읽는다", first is second)

    check("기본 모델은 다국어판이다", "multilingual" in model_registry.DEFAULT_MODEL)

    status = model_registry.status()
    check("상태에 ready 가 실린다", status["ready"] is True)
    check("차원이 보고된다", status["loaded"][0]["dim"] > 0, f"got={status['loaded']}")

    # 학습 모드로 남아 있으면 dropout 때문에 같은 문장이 매번 다른 벡터를 낸다.
    left = model_registry.encode(["고성능 GPU 서버"])
    right = model_registry.encode(["고성능 GPU 서버"])
    check("같은 입력은 같은 벡터를 낸다", float(abs(left - right).max()) < 1e-6)

    check("정규화된 벡터를 낸다", abs(float(left[0] @ left[0]) - 1.0) < 1e-4)
    check("빈 입력은 빈 배열", model_registry.encode([]).shape[0] == 0)

    # ── 오염 ──────────────────────────────────────────────────────────────
    a = DocumentIndex()
    a.add_document("가. GPU: 메모리 96GB 이상", document_id="A")
    b = DocumentIndex()
    b.add_document("나. 사무용 책상 및 의자 납품", document_id="B")

    check("인덱스는 공유되지 않는다", len(a) == 1 and len(b) == 1)
    check(
        "A 의 검색 결과에 B 문서가 섞이지 않는다",
        all(hit.chunk.document_id == "A" for hit in a.search("GPU 메모리", top_k=5)),
    )

    # ── 뜻을 잡는가 ───────────────────────────────────────────────────────
    index = DocumentIndex()
    index.add_document(DOC, document_id="spec-1", chunk_chars=300, overlap_chars=60)
    hits = index.search("그래픽카드 VRAM 용량 요구사항", top_k=1)
    check(
        "유사도가 뜻을 잡는다 — 낱말이 안 겹쳐도 GPU 줄을 찾는다",
        bool(hits) and "GPU" in hits[0].chunk.text,
        f"got={hits[0].chunk.text[:40]!r}" if hits else "결과 없음",
    )
    check("점수가 좌표와 함께 온다", bool(hits) and hits[0].as_dict()["offset"] >= 0)
    check("빈 질의는 빈 결과", index.search("  ") == [])
    check("top_k 를 넘지 않는다", len(index.search("납품", top_k=3)) <= 3)


print()
if FAILURES:
    print(f"실패 {len(FAILURES)}건: {', '.join(FAILURES)}")
    sys.exit(1)
print(f"전부 통과 (건너뜀 {SKIPPED}건)")
