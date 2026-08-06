/**
 * 컨텍스트 클램프. 전부 순수 함수다.
 *
 * 원본(server.js)의 상수만 옮기지 말고 계산식을 옮긴다(Principles §3.5).
 *   - 문자/토큰 = 1.8
 *   - 프롬프트 여유 = 1500 토큰
 *   - SPEC_MAX_CHARS = 50000
 *
 * 그리고 자르되 좌표계를 훼손하지 않는다(§3.3).
 * 잘린 조각은 원본 offset 을 들고 다니고, 인용 검증은 그 offset 으로 되돌린다.
 */

export const SPEC_MAX_CHARS = 50_000;
export const CHARS_PER_TOKEN = 1.8;
export const PROMPT_HEADROOM_TOKENS = 1_500;

export interface SourceDoc {
  documentId: string;
  name: string;
  /** 백엔드가 추출한 원문. 이것이 인용 대조의 기준이다. */
  text: string;
}

export interface ClampedSlice {
  documentId: string;
  name: string;
  text: string;
  /** text 가 원문에서 시작하는 위치. 인용 offset 복원에 쓴다. */
  sourceOffset: number;
  truncated: boolean;
}

export interface ClampResult {
  slices: ClampedSlice[];
  droppedDocumentIds: string[];
  usedChars: number;
  budgetChars: number;
}

export function estimateTokens(chars: number): number {
  return Math.ceil(chars / CHARS_PER_TOKEN);
}

export function charBudget(contextWindowTokens: number, promptOverheadChars: number): number {
  const usable = contextWindowTokens - PROMPT_HEADROOM_TOKENS - estimateTokens(promptOverheadChars);
  return Math.max(0, Math.floor(usable * CHARS_PER_TOKEN));
}

/**
 * 문서를 예산 안으로 줄인다.
 * 문서 순서는 백엔드가 정한 우선순위로 간주하고 유지한다(임의 재정렬 금지).
 */
export function clampToContext(
  docs: readonly SourceDoc[],
  contextWindowTokens: number,
  promptOverheadChars: number,
): ClampResult {
  const budget = Math.min(charBudget(contextWindowTokens, promptOverheadChars), SPEC_MAX_CHARS);
  const slices: ClampedSlice[] = [];
  const dropped: string[] = [];
  let used = 0;

  for (const doc of docs) {
    const remaining = budget - used;
    if (remaining <= 0) {
      dropped.push(doc.documentId);
      continue;
    }
    const take = Math.min(doc.text.length, remaining);
    slices.push({
      documentId: doc.documentId,
      name: doc.name,
      text: doc.text.slice(0, take),
      sourceOffset: 0,
      truncated: take < doc.text.length,
    });
    used += take;
  }

  return { slices, droppedDocumentIds: dropped, usedChars: used, budgetChars: budget };
}
