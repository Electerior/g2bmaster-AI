/**
 * ============================================================================
 * WHAT  — 컨텍스트 클램프 산술의 고정값 검증.
 *         estimateTokens / charBudget / clampToContext 세 순수 함수.
 *
 * WHY   — Principles §3.5: "상수가 아니라 계산식을 이식한다."
 *         원본 server.js 의 상수(1.8 문자/토큰, 1500 토큰 여유, 50000 자)만 옮기고
 *         산술을 새로 짜면 경계에서 조용히 다르게 동작한다. 조용히 다르면
 *         "왜 이 문서만 잘렸지?" 를 몇 주 뒤에 발견한다.
 *         또 클램프 방식은 프롬프트 버전에 들어가는 요소다(§3.1) — 여기가 바뀌면
 *         백엔드의 분석 재사용 키도 바뀌어야 한다.
 *
 * WHERE — src/capability/text/clamp.ts
 *
 * HOW   — 모델도 IO 도 없다. 입력을 주고 출력을 손으로 계산한 값과 대조한다.
 *         계산 과정을 주석에 남겨서, 값이 틀렸을 때 "테스트가 이상한지 코드가
 *         이상한지" 를 다시 유도할 수 있게 한다.
 * ============================================================================
 */

import { describe, expect, it } from 'vitest';

import {
  CHARS_PER_TOKEN,
  PROMPT_HEADROOM_TOKENS,
  SPEC_MAX_CHARS,
  charBudget,
  clampToContext,
  estimateTokens,
  type SourceDoc,
} from '../../src/capability/text/clamp.js';

/** 원본에서 굳어진 상수. 바뀌면 프롬프트 버전이 바뀌어야 한다는 뜻이다. */
describe('이식된 상수', () => {
  it('원본 값 그대로다', () => {
    expect(SPEC_MAX_CHARS).toBe(50_000);
    expect(CHARS_PER_TOKEN).toBe(1.8);
    expect(PROMPT_HEADROOM_TOKENS).toBe(1_500);
  });
});

describe('estimateTokens', () => {
  it('문자/1.8 을 올림한다', () => {
    // 1800 / 1.8 = 1000 (정확히 나누어떨어지는 지점)
    expect(estimateTokens(1_800)).toBe(1_000);
    // 1 / 1.8 = 0.55… → 올림 1. 내림이면 0 이 되어 "공짜 텍스트" 가 생긴다.
    expect(estimateTokens(1)).toBe(1);
    expect(estimateTokens(0)).toBe(0);
  });
});

describe('charBudget', () => {
  it('여유 토큰과 프롬프트 오버헤드를 뺀 뒤 문자로 되돌린다', () => {
    // (32768 - 1500 여유 - 0 오버헤드) * 1.8 = 31268 * 1.8 = 56282.4 → 내림 56282
    expect(charBudget(32_768, 0)).toBe(56_282);
  });

  it('오버헤드도 토큰으로 환산해서 뺀다', () => {
    // 오버헤드 180자 = 100 토큰. (32768 - 1500 - 100) * 1.8 = 31168 * 1.8 = 56102.4 → 56102
    expect(charBudget(32_768, 180)).toBe(56_102);
  });

  it('여유보다 작은 컨텍스트 창이면 0 이다 (음수 예산 금지)', () => {
    // 음수가 새어 나가면 slice(0, 음수) 가 되어 뒤에서부터 잘린 텍스트를 프롬프트에
    // 넣게 된다 — 좌표계가 깨지고 인용이 전부 기각된다(§3.3).
    expect(charBudget(1_500, 0)).toBe(0);
    expect(charBudget(1_024, 0)).toBe(0);
  });
});

describe('clampToContext', () => {
  const doc = (documentId: string, chars: number, fill = 'ㄱ'): SourceDoc => ({
    documentId,
    name: `${documentId}.hwp`,
    text: fill.repeat(chars),
  });

  it('SPEC_MAX_CHARS 가 컨텍스트 창보다 먼저 걸린다', () => {
    // charBudget(32768,0) = 56282 > 50000 이므로 최종 예산은 50000 이어야 한다.
    const result = clampToContext([doc('a', 10)], 32_768, 0);
    expect(result.budgetChars).toBe(SPEC_MAX_CHARS);
  });

  it('예산을 넘는 문서는 자르고 truncated 로 표시한다', () => {
    // 예산 50000. a 가 30000 을 먹고, b 는 남은 20000 만 들어간다.
    const result = clampToContext([doc('a', 30_000), doc('b', 30_000)], 32_768, 0);

    expect(result.slices).toHaveLength(2);
    expect(result.slices[0]).toMatchObject({ documentId: 'a', truncated: false });
    expect(result.slices[0]?.text).toHaveLength(30_000);
    expect(result.slices[1]).toMatchObject({ documentId: 'b', truncated: true });
    expect(result.slices[1]?.text).toHaveLength(20_000);
    expect(result.usedChars).toBe(50_000);
    expect(result.droppedDocumentIds).toEqual([]);
  });

  it('예산이 다 찬 뒤의 문서는 잘리는 게 아니라 버려지고, 버린 사실을 보고한다', () => {
    // WHY: 조용히 버리면 사용자는 "이 첨부는 왜 반영이 안 됐지?" 를 알 수 없다.
    //      droppedDocumentIds 는 sourceTrace 로 나가는 산출물이다(Principles §5.2).
    const result = clampToContext([doc('a', 50_000), doc('b', 10), doc('c', 10)], 32_768, 0);

    expect(result.slices.map((s) => s.documentId)).toEqual(['a']);
    expect(result.droppedDocumentIds).toEqual(['b', 'c']);
  });

  it('백엔드가 준 문서 순서를 유지한다', () => {
    // WHY: 문서 순서는 백엔드가 정한 우선순위다. 여기서 길이순 같은 걸로 재정렬하면
    //      중요한 문서가 예산 밖으로 밀려나는 일이 재현 불가능하게 일어난다.
    const result = clampToContext([doc('z', 10), doc('a', 10), doc('m', 10)], 32_768, 0);
    expect(result.slices.map((s) => s.documentId)).toEqual(['z', 'a', 'm']);
  });

  it('원본 좌표계를 훼손하지 않는다 — 슬라이스는 원문 접두사이고 offset 은 0 이다', () => {
    // WHY: §3.3. 자른 조각이 원문의 어디서 시작하는지 모르면 인용 offset 을 되돌릴 수
    //      없고, 백엔드의 원문 대조에서 전부 기각된다.
    const original = doc('a', 100, 'ㄴ');
    const result = clampToContext([original], 32_768, 0);
    const slice = result.slices[0];

    expect(slice?.sourceOffset).toBe(0);
    expect(original.text.startsWith(slice?.text ?? 'X')).toBe(true);
  });

  it('예산이 0 이면 아무것도 담지 못하고 usedChars 가 0 이다', () => {
    // WHY: 호출부(clampStep)가 이 0 을 보고 INPUT_TOO_LARGE 라는 정직한 실패를 만든다.
    //      빈 슬라이스로 그냥 진행하면 LLM 이 아무 근거 없이 요약을 지어낸다.
    const result = clampToContext([doc('a', 10)], 1_024, 0);

    expect(result.budgetChars).toBe(0);
    expect(result.usedChars).toBe(0);
    expect(result.slices).toEqual([]);
    expect(result.droppedDocumentIds).toEqual(['a']);
  });

  it('문서가 없으면 빈 결과다 (예외를 던지지 않는다)', () => {
    // 빈 documents 의 처리는 스텝의 책임이다 — 순수 함수는 판단하지 않는다.
    const result = clampToContext([], 32_768, 0);
    expect(result).toMatchObject({ slices: [], droppedDocumentIds: [], usedChars: 0 });
  });
});
