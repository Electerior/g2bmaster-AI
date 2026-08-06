/**
 * ============================================================================
 * WHAT  — 인용 자체 검증(verifyFacts)의 채택/기각 규칙.
 *
 * WHY   — ai-boundary.md §4: 근거 채택의 최종 판정은 백엔드가 원문 대조로 한다.
 *         "AI 응답을 검증하는 쪽이 AI 자신이면 검증이 아니다."
 *         그래서 우리 몫은 **검증을 통과할 수 있는 형태로 만들어 보내는 것**이고,
 *         통과 못 할 인용은 여기서 버려야 한다. 백엔드에서 기각당하면 사용자에게는
 *         "근거 없음" 으로만 보이고 원인이 안 보인다(Principles §2.4).
 *
 * WHERE — src/capability/text/citation.ts
 *
 * HOW   — 원문(SourceDoc)과 슬라이스(ClampedSlice)를 직접 만들어 넣고,
 *         LLM 이 흔히 저지르는 네 가지 상황을 재현한다:
 *           (1) 정확한 인용, (2) 인용은 맞는데 offset 이 틀림,
 *           (3) 없는 문서를 지목, (4) 원문에 없는 문구를 지어냄.
 * ============================================================================
 */

import { describe, expect, it } from 'vitest';

import { toSourceOffset, verifyFacts, type Fact } from '../../src/capability/text/citation.js';
import type { ClampedSlice, SourceDoc } from '../../src/capability/text/clamp.js';

/** 원문. 인용 대조의 유일한 기준이다. */
const ORIGINAL: SourceDoc = {
  documentId: 'doc-1',
  name: '규격서.hwp',
  text: '납품 기한은 계약일로부터 30일 이내로 한다. 하자보수 기간은 2년이다.',
};

/** 잘리지 않은 슬라이스. sourceOffset 0. */
const SLICE: ClampedSlice = {
  documentId: 'doc-1',
  name: '규격서.hwp',
  text: ORIGINAL.text,
  sourceOffset: 0,
  truncated: false,
};

function fact(quote: string, offset: number, length = quote.length, documentId = 'doc-1'): Fact {
  return { statement: '테스트 사실', evidence: { documentId, quote, offset, length } };
}

describe('toSourceOffset', () => {
  it('슬라이스 기준 offset 을 원문 기준으로 되돌린다', () => {
    // WHY: 클램프가 문서 중간부터 잘라 담기 시작하면 LLM 이 보는 좌표와 원문 좌표가
    //      sourceOffset 만큼 어긋난다. 이 보정이 없으면 잘린 문서의 인용은 전부 기각된다.
    const shifted: ClampedSlice = { ...SLICE, sourceOffset: 100 };
    expect(toSourceOffset(shifted, 7)).toBe(107);
  });
});

describe('verifyFacts', () => {
  it('offset·length 가 원문과 축자 일치하면 그대로 채택한다', () => {
    const offset = ORIGINAL.text.indexOf('30일');
    const { accepted, rejected } = verifyFacts([fact('30일', offset)], [ORIGINAL], [SLICE]);

    expect(rejected).toEqual([]);
    expect(accepted).toHaveLength(1);
    expect(accepted[0]?.evidence).toMatchObject({ quote: '30일', offset, length: 3 });
  });

  it('인용은 맞는데 offset 이 틀리면 위치를 되찾아 채택한다', () => {
    // WHY: 모델이 문구는 정확히 베끼면서 위치 숫자만 흘리는 것은 매우 흔하다.
    //      여기서 버리면 멀쩡한 근거를 잃는다. 대신 **문구가 원문에 실재할 때만** 되찾는다.
    // HOW: 일부러 엉뚱한 offset(0)을 주고, 반환된 offset 이 실제 위치로 교정됐는지 본다.
    const { accepted, rejected } = verifyFacts([fact('하자보수', 0)], [ORIGINAL], [SLICE]);

    expect(rejected).toEqual([]);
    expect(accepted[0]?.evidence.offset).toBe(ORIGINAL.text.indexOf('하자보수'));
    expect(accepted[0]?.evidence.length).toBe('하자보수'.length);
  });

  it('교정된 offset 으로 원문을 다시 잘라내면 인용과 정확히 같다', () => {
    // WHY: 이것이 백엔드가 실제로 수행할 검증이다. 우리 출력이 그 검증을 통과하는지
    //      여기서 시뮬레이션한다 — 통과하지 못할 값을 내보내면 안 된다.
    const { accepted } = verifyFacts([fact('2년', 0)], [ORIGINAL], [SLICE]);
    const ev = accepted[0]?.evidence;

    expect(ORIGINAL.text.slice(ev?.offset ?? -1, (ev?.offset ?? 0) + (ev?.length ?? 0))).toBe('2년');
  });

  it('모르는 문서를 지목하면 기각한다', () => {
    // WHY: 모델이 문서 ID 를 지어내는 경우. 통과시키면 백엔드가 대조할 원문 자체가 없다.
    const { accepted, rejected } = verifyFacts([fact('30일', 0, 2, 'doc-없음')], [ORIGINAL], [SLICE]);

    expect(accepted).toEqual([]);
    expect(rejected[0]?.reason).toBe('unknown-document');
  });

  it('원문에 없는 문구를 지어내면 기각한다 — offset 자리에 다른 글자가 있는 경우', () => {
    const { accepted, rejected } = verifyFacts([fact('무상 유지보수 5년', 0)], [ORIGINAL], [SLICE]);

    expect(accepted).toEqual([]);
    expect(rejected[0]?.reason).toBe('offset-mismatch');
  });

  it('원문에 없는 문구를 지어내면 기각한다 — offset 이 원문 범위 밖인 경우', () => {
    const { accepted, rejected } = verifyFacts([fact('무상 유지보수 5년', 9_999)], [ORIGINAL], [SLICE]);

    expect(accepted).toEqual([]);
    expect(rejected[0]?.reason).toBe('quote-not-found');
  });

  it('빈 인용은 기각한다', () => {
    // WHY: 빈 문자열은 어떤 원문에서도 "발견" 되므로 축자 대조를 그냥 통과한다.
    //      우리도 백엔드도 통과시키면 근거 없는 사실이 채택된다 — 검증이 무력화된다.
    //      (이 규칙이 없으면 조용히 뚫리는 구멍이라 명시 케이스로 박아 둔다.)
    const { accepted, rejected } = verifyFacts([fact('', 0, 0)], [ORIGINAL], [SLICE]);

    expect(accepted).toEqual([]);
    expect(rejected[0]?.reason).toBe('quote-not-found');
  });

  it('채택과 기각을 한 번에 갈라낸다 — 하나가 틀려도 나머지를 살린다', () => {
    // WHY: 전부 아니면 전무로 처리하면 모델의 사소한 실수 하나가 요약 전체를 날린다.
    const facts = [fact('30일', ORIGINAL.text.indexOf('30일')), fact('없는문구', 0)];
    const { accepted, rejected } = verifyFacts(facts, [ORIGINAL], [SLICE]);

    expect(accepted).toHaveLength(1);
    expect(rejected).toHaveLength(1);
  });

  it('입력 Fact 를 변형하지 않는다 (불변)', () => {
    // WHY: 호출부는 rejected 목록을 로그에 남긴다. 여기서 원본을 고쳐 버리면
    //      "모델이 실제로 무엇을 줬는가" 라는 조사 단서가 사라진다.
    const input = fact('하자보수', 0);
    const before = structuredClone(input);
    verifyFacts([input], [ORIGINAL], [SLICE]);

    expect(input).toEqual(before);
  });
});
