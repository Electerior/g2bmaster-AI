/**
 * 인용 자체 검증.
 *
 * 최종 채택은 백엔드가 원문 대조로 판정한다(ai-boundary.md §4).
 * 우리는 그 검증을 회피하지 않고, 통과할 수 있는 형태로 만들어 보낸다.
 * 불일치 인용은 여기서 버린다 — 백엔드에서 기각당하면 원인이 보이지 않는다.
 */

import type { ClampedSlice, SourceDoc } from './clamp.js';

export interface Evidence {
  documentId: string;
  quote: string;
  /** 원문 기준 시작 위치. LLM 이 준 값은 슬라이스 기준이므로 보정한다. */
  offset: number;
  length: number;
}

export interface Fact<T = unknown> {
  statement: string;
  evidence: Evidence;
  extra?: T;
}

export interface VerifyResult<T> {
  accepted: Fact<T>[];
  rejected: Array<{ fact: Fact<T>; reason: 'unknown-document' | 'offset-mismatch' | 'quote-not-found' }>;
}

/** 슬라이스 기준 offset 을 원문 기준으로 되돌린다. */
export function toSourceOffset(slice: ClampedSlice, sliceOffset: number): number {
  return slice.sourceOffset + sliceOffset;
}

export function verifyFacts<T>(
  facts: readonly Fact<T>[],
  originals: readonly SourceDoc[],
  slices: readonly ClampedSlice[],
): VerifyResult<T> {
  const originalById = new Map(originals.map((d) => [d.documentId, d]));
  const sliceById = new Map(slices.map((s) => [s.documentId, s]));

  const accepted: Fact<T>[] = [];
  const rejected: VerifyResult<T>['rejected'] = [];

  for (const fact of facts) {
    // 빈 인용은 근거가 아니다.
    // 아래 축자 대조는 `''` 를 원문의 아무 위치에서나 찾아내므로 그냥 두면 통과해 버리고,
    // 백엔드의 원문 대조도 똑같이 통과시킨다 — 근거 없는 사실이 채택된다(Principles §2.4).
    if (fact.evidence.quote.length === 0) {
      rejected.push({ fact, reason: 'quote-not-found' });
      continue;
    }

    const original = originalById.get(fact.evidence.documentId);
    const slice = sliceById.get(fact.evidence.documentId);
    if (!original || !slice) {
      rejected.push({ fact, reason: 'unknown-document' });
      continue;
    }

    const offset = toSourceOffset(slice, fact.evidence.offset);
    const at = original.text.slice(offset, offset + fact.evidence.length);

    if (at === fact.evidence.quote) {
      accepted.push({ ...fact, evidence: { ...fact.evidence, offset } });
      continue;
    }

    // LLM 이 offset 을 흘렸을 뿐 인용 자체는 정확한 경우가 흔하다. 위치를 되찾아준다.
    const found = original.text.indexOf(fact.evidence.quote);
    if (found >= 0 && fact.evidence.quote.length > 0) {
      accepted.push({
        ...fact,
        evidence: { ...fact.evidence, offset: found, length: fact.evidence.quote.length },
      });
      continue;
    }

    rejected.push({ fact, reason: at.length > 0 ? 'offset-mismatch' : 'quote-not-found' });
  }

  return { accepted, rejected };
}
