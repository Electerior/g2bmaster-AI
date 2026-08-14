"""사업기회 분류 — 공고 한 건을 그룹사·제품군·적합도로 가른다.

`item-summary` 의 마지막 스텝(items 추출)이 쓸 판정이지만, 지금은 **HTTP 표면이 아니다.**
백엔드 첨부 파싱이 없어 `item-summary` 를 온전히 이식할 수 없으므로, 먼저 공고 메타데이터만으로
되는 부분을 떼어 실측 가능한 형태로 만든 것이다. 새 엔드포인트를 열려면 `CLAUDE.md §8` 대로
`docs/decisions.md` 에 적고 백엔드와 합의해야 한다 — 여기서 마음대로 열지 않는다.

**분류 체계는 코드가 아니라 프롬프트에 있다.** 그룹사가 하나 늘 때 재배포가 필요하면 안 된다.

실측 이력은 `docs/opportunity-eval.md`.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .llm.client import batch_concurrency, lms_chat, loaded_model
from .prompts import OPPORTUNITY_PROMPT_VERSION

#: 한 번에 묶어 보낼 공고 수. 크게 잡을수록 싸지만 모델이 항목을 빠뜨릴 확률이 오른다
#: (v1 실측: 10건 묶음에서 726건 중 10건 유실). 유실은 아래 재요청으로 메운다.
BATCH_SIZE = 10

#: 배치에서 빠진 항목을 다시 물을 때의 묶음 크기. 1 이면 가장 확실하지만 느리다.
RETRY_BATCH_SIZE = 3

SUBSIDIARIES = ("서버스테이션", "브레인웨어", "일렉테리어", "렌탈본점", "확인필요")
WORK_KINDS = ("물품", "용역", "공사")
FIT_LEVELS = ("High", "Mid", "Low")

# ── 프롬프트 ─────────────────────────────────────────────────────────────────
# 아래 분류 체계는 KBID 워크북 726건의 실제 배정을 읽어 만든 것이다. 추측이 아니다.
# 고치면 prompts.OPPORTUNITY_PROMPT_VERSION 을 함께 올린다.
SYSTEM_PROMPT = """너는 나라장터 입찰공고를 그룹사 사업기회로 분류하는 분석가다.

## 그룹사와 취급 품목

- 서버스테이션 : GPU, 서버, 워크스테이션, 병렬컴퓨터, 스토리지·백업장치, 네트워크 장비
- 브레인웨어   : 사무용 PC·데스크탑·노트북·태블릿·스마트단말기, 소프트웨어·라이선스
- 일렉테리어   : LED·전광판·사이니지·전자칠판, AV·방송·음향·영상 장비, 전화교환기,
                촬영·감시용 드론
- 렌탈본점     : 생활가전 — 세탁기·건조기·냉장고·정수기·제습기·공기청정기·식기세척기·
                커피머신 등. **구매든 임차든 상관없다.** 그 밖의 품목이라도 계약이
                임차·렌탈·리스면 여기로 본다
- 확인필요     : 위 어디에도 속하지 않음

## 판정 순서

1. 업무구분을 정한다 — 물품 / 용역 / 공사.
   **업무구분은 자회사 판정을 덮지 않는다.** 정수기 임차처럼 실제로는 우리 품목인데
   나라장터에 '용역'으로 올라오는 건이 많다. 둘을 따로 판정한다.
2. 자회사를 정한다. 품목이 여러 그룹사에 걸치면 공고명에서 **주된 조달 대상**을 고른다.
3. fit 을 정한다.
   - High : 서버스테이션의 GPU·서버·스토리지 주력 건, 또는 렌탈본점 건 전부
   - Mid  : 취급 범위에는 들지만 주력이 아닌 건 (사무용 PC, 일반 SW, AV 장비 등)
   - Low  : 확인필요
4. 근거는 공고명에서 **실제로 읽은 단서만** 25자 이내로 적는다. 지어내지 않는다.
   품목을 알 수 없으면 자회사를 "확인필요"로 두고 근거에 무엇이 불분명한지 적는다.

## 출력

각 공고에 대해 아래 객체를 만들어 **JSON 배열 하나로만** 답한다.
설명도 코드펜스도 붙이지 않는다. `i` 는 입력에 붙은 번호를 그대로 쓴다.

[{"i":1,"업무구분":"물품","자회사":"서버스테이션","제품":"GPU","fit":"High","근거":"GPU 서버 구매"}]"""


@dataclass
class Notice:
    """분류 입력. 공고 메타데이터만 쓴다 — 첨부는 백엔드가 아직 주지 못한다."""

    name: str
    institution: str = ""
    amount: float | None = None
    method: str = ""
    region: str = ""

    def as_line(self, index: int) -> str:
        amount = f"{self.amount}억" if self.amount else "금액미상"
        parts = [f"{index}. {self.name}"]
        if self.institution:
            parts.append(f"발주:{self.institution}")
        parts.extend([amount, self.method or "-", self.region or "-"])
        return " | ".join(parts)


@dataclass
class Verdict:
    work_kind: str
    subsidiary: str
    product: str
    fit: str
    reason: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Verdict | None":
        """모델 출력을 값 집합으로 좁힌다. 밖의 값은 버린다 — 조용히 통과시키면 집계가 오염된다."""
        subsidiary = str(payload.get("자회사") or "").strip()
        work_kind = str(payload.get("업무구분") or "").strip()
        fit = str(payload.get("fit") or "").strip()
        if subsidiary not in SUBSIDIARIES:
            return None
        return cls(
            work_kind=work_kind if work_kind in WORK_KINDS else "",
            subsidiary=subsidiary,
            product=str(payload.get("제품") or "").strip(),
            fit=fit if fit in FIT_LEVELS else "",
            reason=str(payload.get("근거") or "").strip()[:60],
        )


@dataclass
class RunStats:
    """실측용. '돌아갔다'가 아니라 '얼마나 새어 나갔다'를 말할 수 있어야 한다."""

    total: int = 0
    calls: int = 0
    retried: int = 0
    recovered_by_retry: int = 0
    missing: int = 0
    rejected: int = 0
    failed_batches: list[str] = field(default_factory=list)


def _parse_array(text: str) -> list[dict[str, Any]]:
    """배열 앞뒤에 무엇이 붙었든 첫 JSON 배열만 건져 낸다."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except ValueError:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _build_request(model: str, batch: Sequence[Notice]) -> dict:
    """한 묶음의 채팅 요청 본문. 전송은 호출부(병렬 gather)가 한다."""
    user = f"공고 {len(batch)}건:\n" + "\n".join(
        notice.as_line(n) for n, notice in enumerate(batch, start=1)
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 220 * len(batch) + 200,
    }


def _parse_batch(content: str, batch_len: int, stats: RunStats) -> dict[int, Verdict]:
    """응답 본문 → {batch 안 0-기반 위치: Verdict}. 값 집합 밖은 버리고 rejected 로 센다."""
    out: dict[int, Verdict] = {}
    for entry in _parse_array(content):
        index = entry.get("i")
        if not isinstance(index, int) or not (1 <= index <= batch_len):
            continue
        verdict = Verdict.from_payload(entry)
        if verdict is None:
            stats.rejected += 1
            continue
        out[index - 1] = verdict
    return out


async def _classify_batch(model: str, batch: Sequence[Notice], stats: RunStats) -> dict[int, Verdict]:
    """한 묶음을 분류한다(단건 경로 — 재요청에서 쓴다). 키는 batch 안 0-기반 위치."""
    stats.calls += 1
    response = await lms_chat(_build_request(model, batch))
    return _parse_batch(response["choices"][0]["message"]["content"], len(batch), stats)


async def classify(notices: Sequence[Notice], *, progress=None) -> tuple[list[Verdict | None], RunStats]:
    """공고 목록을 분류한다. 반환 순서는 입력 순서와 같고, 못 채운 자리는 None 이다.

    **묶어 보내면 모델이 항목을 빠뜨린다.** v1 은 그 유실을 세지도 않아 726건 중 10건이
    조용히 사라졌다. 여기서는 빠진 자리를 골라 다시 묻고, 그래도 비면 None 으로 남겨
    `RunStats.missing` 에 센다 — 없는 것을 있는 척하지 않는다.
    """
    model = await loaded_model()
    stats = RunStats(total=len(notices))
    results: list[Verdict | None] = [None] * len(notices)

    # ── 1차: 배치를 **동시에** 돌린다 ────────────────────────────────────────
    # 순차로 돌면 GPU 가 네 대여도 매 순간 한 대만 일한다. gather 로 한꺼번에 던지면
    # 워커 풀이 여유율 기준으로 흩뿌린다. 동시성은 건강한 워커의 총 용량으로 제한한다 —
    # 그보다 많이 던져 봐야 처리량은 용량에서 막히고 대기자만 쌓인다.
    limit = batch_concurrency()
    semaphore = asyncio.Semaphore(limit)
    done = 0

    async def run_batch(offset: int, batch: Sequence[Notice]) -> None:
        nonlocal done
        stats.calls += 1
        async with semaphore:
            try:
                response = await lms_chat(_build_request(model, batch))
                filled = _parse_batch(response["choices"][0]["message"]["content"], len(batch), stats)
                for position, verdict in filled.items():
                    results[offset + position] = verdict
            except Exception as error:  # noqa: BLE001 — 한 묶음 실패로 전체를 멈추지 않는다
                stats.failed_batches.append(f"{offset}: {type(error).__name__} {str(error)[:120]}")
        done += len(batch)
        if progress:
            progress(done, len(notices))

    await asyncio.gather(*(
        run_batch(offset, notices[offset : offset + BATCH_SIZE])
        for offset in range(0, len(notices), BATCH_SIZE)
    ))

    # ── 빠진 자리 재요청 ─────────────────────────────────────────────────────
    holes = [i for i, verdict in enumerate(results) if verdict is None]
    for start in range(0, len(holes), RETRY_BATCH_SIZE):
        chunk = holes[start : start + RETRY_BATCH_SIZE]
        stats.retried += len(chunk)
        try:
            filled = await _classify_batch(model, [notices[i] for i in chunk], stats)
        except Exception as error:  # noqa: BLE001
            stats.failed_batches.append(f"retry {chunk}: {type(error).__name__} {str(error)[:120]}")
            continue
        for position, verdict in filled.items():
            results[chunk[position]] = verdict
            stats.recovered_by_retry += 1

    stats.missing = sum(1 for verdict in results if verdict is None)
    return results, stats


def prompt_version() -> str:
    return OPPORTUNITY_PROMPT_VERSION


def notices_from_rows(rows: Iterable[dict[str, Any]]) -> list[Notice]:
    """워크북 등 표 형태 입력을 Notice 로 옮긴다."""
    return [
        Notice(
            name=str(row.get("공고명") or "").strip(),
            institution=str(row.get("공고기관") or "").strip(),
            amount=row.get("금액억"),
            method=str(row.get("계약방법") or "").strip(),
            region=str(row.get("지역제한") or "").strip(),
        )
        for row in rows
    ]


if __name__ == "__main__":  # 값 집합 좁히기가 실제로 도는지만 확인한다
    assert Verdict.from_payload({"자회사": "없는회사"}) is None, "값 집합 밖은 버려야 한다"
    ok = Verdict.from_payload({"자회사": "브레인웨어", "업무구분": "물품", "fit": "높음"})
    assert ok is not None and ok.fit == "", "fit 이 값 집합 밖이면 비워 둔다"
    assert _parse_array('```json\n[{"i":1}]\n```') == [{"i": 1}], "코드펜스를 벗겨야 한다"
    assert _parse_array("설명입니다") == [], "배열이 없으면 빈 목록"
    print("opportunity.py: OK")
    print(asyncio.run(loaded_model()) if False else "", end="")
