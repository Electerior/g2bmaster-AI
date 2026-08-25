#!/usr/bin/env python3
"""규격해석 품질 회귀 — LLM이 공고 규격을 해석했을 때 정답(최종 워크북)과 일치하는지 대조.

이전 충실성 회귀(test_migration_fidelity.py)는 **결정론적 코드**가 같은 출력을 내는지 보는 것이라면,
여기는 파이프라인의 "AI 몫"(상품 발굴·규격 대조·예외 해석)의 품질을 보는 것이다.
게이트(URL·추정가)는 모델 무관하게 작동하지만, 규격 오탐(예: DDR5-5600을 5200으로)은
모델의 이해력 문제라 별도 고정 fixture로 회귀한다.

고정 케이스(bidpipe/fixtures/<case>/):
  spec_excerpt.txt  — 규격서 원문 중 핵심 구절 (LLM 입력, 가격·시세 정보 불포함)
  expected.json     — 정답: 최종 워크북의 물품행에서 추출 (required/forbidden/must_mention/should_mention)
  meta.json         — 공고 메타 (제목·기관·날짜)

동작:
  1. spec_excerpt + 파이프라인 규칙(추정가 금지·묶음 행 금지 등) 프롬프트로 LLM 호출
  2. JSON 응답을 expected.json과 대조
  3. PASS(전부 일치) / WARN(should_mention 누락) / FAIL(required 누락·forbidden 위반·must_mention 누락)

실행:
  make bidpipe-fixture            # 전체 케이스
  make bidpipe-fixture CASE=jh_gpu_server

LLM은 .env(LMS_BASE/LLM_API_KEY/LMS_MODEL)에서 읽어 app.config와 동일한 경로를 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import sys

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[2]   # repo 루트 (bidpipe/tests → bidpipe → repo)
FIX = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS") or "120")

SYSTEM_PROMPT = """\
물품구매 입찰 분석을 하는 조달 전문 AI다. 아래 공고 규격을 해석해 물품행 리스트를 작성한다.

규칙 (위반 시 판정이 뒤집히는 사고가 실제로 발생함):
1. 추정가 금지 — 가격·단가는 이 단계에서 쓰지 않는다. 이 단계는 규격 해석만 한다.
2. 묶음 행 금지 — 조립 PC/서버는 부품별로 행을 펼친다. "기타 일괄" 한 줄 금지.
   명세서 순번을 하나도 빠짐없이 어느 행엔가 매핑한다.
3. 대안 후보(규격 완화 시나리오, 참고품, 규격미달)는 sel="X" 행으로 함께 기록한다.
4. 규격서에 명시되지 않았으나 조립/도급에 필수인 항목(예: 파워)도 행으로 넣고
   비고에 근거(조항 인용)를 쓴다.
5. 행마다 spec_ref에 대조한 규격서 원문 구절을 인용한다.
6. 응답은 JSON만 출력한다. 마크다운·설명·코드펜스 없이.

출력 형식:
{
  "items": [
    {"name": "품명(모델명 포함)", "spec_ref": "대조한 규격서 원문", "qty": 1,
     "sel": "O", "note": "충족 판정·근거·주의사항"}
  ],
  "notes": "전체적인 해석 메모 (규격 오류·누락·문의 필요 사항)"
}
sel: 선택(O) / 대안·참고(X)
"""


def build_prompt(case_dir: pathlib.Path) -> str:
    meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))
    spec = (case_dir / "spec_excerpt.txt").read_text(encoding="utf-8")
    return (
        f"## 공고\n- 공고번호: {meta['bid']}\n- 기관: {meta['org']}\n- 제목: {meta['title']}\n\n"
        f"## 규격서 전문 (핵심 구절)\n{spec}\n\n"
        "위 규격에 맞는 물품행 리스트를 작성해 JSON으로 답하라."
    )


def extract_json(text: str) -> dict:
    """응답에서 JSON 객체를 뽑는다 (코드펜스·여백 허용)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"JSON 없음: {text[:200]!r}")
        text = text[start:end + 1]
    return json.loads(text)


def norm(s) -> str:
    return re.sub(r"\s+", "", str(s or "").lower())


def qty_satisfied(qty, name: str, qty_min: int) -> bool:
    """수량 일치 — qty 필드 또는 품명의 '×2'/'x2' 표기까지 본다."""
    try:
        q = int(qty)
    except (TypeError, ValueError):
        q = 0
    if q >= qty_min:
        return True
    m = re.search(r"[×x*]\s*(\d+)", str(name))
    return bool(m and int(m.group(1)) >= qty_min)


def check_case(case_dir: pathlib.Path, llm_items: list[dict], full_text: str) -> dict:
    """대조 규칙:
    - required_items.name_kws: **all-of** (한 행의 품명에 모두 포함) — 단, 목록은 스펙에
      나오는 용어만 쓴다. 정답 워크북의 브랜드명(CORSAIR FRAME 같은)을 넣으면 LLM이
      재현할 수 없는 요구가 된다. 순수 동의어 목록이면 any_of:true 로.
    - forbidden.name_kws: all-of (sel=O 행의 품명에 모두 포함이면 위반)
    - must/should_mention.kws: **any-of** (동의어 중 하나만 전체 응답 어디엔가 있으면 충족)
    """
    exp = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    llm_norm = [
        {"name": norm(i.get("name")), "qty": i.get("qty"), "sel": str(i.get("sel", "")).upper(),
         "blob": norm((i.get("name") or "") + " " + (i.get("note") or "") + " " + (i.get("spec_ref") or ""))}
        for i in llm_items
    ]
    all_blob = norm(full_text)
    failures, warnings, passes = [], [], []

    # required: 각 항목에 대해 sel=O 행 중 name_kws 충족 + 수량 충족
    for req in exp.get("required_items", []):
        kws = [norm(k) for k in req["name_kws"]]
        mode = req.get("any_of", False)
        hit = None
        for it in llm_norm:
            if it["sel"] != "O":
                continue
            ok = any(k in it["name"] for k in kws) if mode else all(k in it["name"] for k in kws)
            if ok and qty_satisfied(it["qty"], it["name"], req.get("qty_min", 1)):
                hit = it
                break
        if hit:
            passes.append(f"required: {req['desc']}")
        else:
            failures.append(f"required 누락: {req['desc']}")

    # forbidden: sel=O 행에 kws(모두) 포함이면 위반
    for fb in exp.get("forbidden", []):
        kws = [norm(k) for k in fb["name_kws"]]
        for it in llm_norm:
            if it["sel"] == "O" and all(k in it["name"] for k in kws):
                failures.append(f"forbidden 위반: {fb['reason']} → 행 {it['name'][:40]}")

    # must_mention: 전체 응답 텍스트 어디엔가 동의어 중 하나만 있으면 충족
    for mm in exp.get("must_mention", []):
        kws = [norm(k) for k in mm["kws"]]
        if not any(k in all_blob for k in kws):
            failures.append(f"must_mention 누락: {mm['reason']} (kws={mm['kws']})")

    # should_mention: 같은 방식이지만 경고 수준
    for sm in exp.get("should_mention", []):
        kws = [norm(k) for k in sm["kws"]]
        if not any(k in all_blob for k in kws):
            warnings.append(f"should_mention 누락: {sm['reason']} (kws={sm['kws']})")

    n_items = len(llm_items)
    n_req = len(exp.get("required_items", []))
    n_sel = sum(1 for it in llm_norm if it["sel"] == "O")
    return {
        "failures": failures, "warnings": warnings, "passes": passes,
        "score": f"{len(passes)}/{n_req} required · O행 {n_sel}/{n_items}행",
    }


async def run_llm(prompt: str) -> str:
    base = os.getenv("LMS_BASE", "").rstrip("/")
    key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LMS_MODEL", "")
    if not base or not model:
        raise SystemExit("LMS_BASE / LMS_MODEL 미설정 (.env 확인)")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 4096,
        "reasoning_effort": "none",
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for attempt in (1, 2):
            r = await client.post(f"{base}/v1/chat/completions", json=body, headers=headers)
            if r.is_error:
                raise SystemExit(f"LLM HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            try:
                extract_json(content)
                return content
            except ValueError:
                if attempt == 2:
                    return content
                # JSON 파싱 실패 → 재시도
                body["messages"].append({"role": "assistant", "content": content})
                body["messages"].append(
                    {"role": "user", "content": "JSON 파싱에 실패했다. 코드펜스 없이 JSON 객체 하나만 다시 출력해라."}
                )
    return ""


async def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    cases = sorted(
        d for d in FIX.iterdir()
        if d.is_dir() and (d / "expected.json").exists() and (only is None or d.name == only)
    )
    if not cases:
        print(f"SKIP: fixture 없음 ({FIX})" + (f" — CASE={only}" if only else ""))
        return 0

    from app.llm.client import llm_status  # noqa: E402
    status = await asyncio.wait_for(llm_status(), timeout=20)
    print(f"LLM: base={status['base']} reachable={status['reachable']} model={status['loadedModel'] or os.getenv('LMS_MODEL')}")
    if not status["reachable"]:
        print("SKIP: LLM 미도달")
        return 0

    n_fail = 0
    for case in cases:
        print(f"\n=== {case.name} ===")
        prompt = build_prompt(case)
        content = await run_llm(prompt)
        # 응답 덤프 — FAIL 원인 분석용 (gitignore 대상)
        (case / "last_response.json").write_text(
            json.dumps({"prompt_chars": len(prompt), "response": content},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            data = extract_json(content)
        except ValueError as e:
            print(f"  FAIL: LLM 응답이 JSON이 아님 — {e}")
            n_fail += 1
            continue
        items = data.get("items") or []
        result = check_case(case, items, content)
        print(f"  {result['score']}")
        for p in result["passes"]:
            print(f"    ✓ {p}")
        for w in result["warnings"]:
            print(f"    ⚠ {w}")
        for f_ in result["failures"]:
            print(f"    ✗ {f_}")
        if result["failures"]:
            n_fail += 1
            print(f"  → FAIL")
        else:
            print(f"  → {'PASS(경고 ' + str(len(result['warnings'])) + ')' if result['warnings'] else 'PASS'}")

    print(f"\n{'=' * 50}")
    print(f"결과: {len(cases) - n_fail}/{len(cases)} 케이스 통과" + (f" — {n_fail}건 FAIL" if n_fail else " — 전부 PASS"))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
