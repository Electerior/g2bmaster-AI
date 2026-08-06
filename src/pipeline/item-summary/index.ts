/**
 * item-summary 파이프라인.
 *
 * 분할 후 우리 몫은 4스텝이다(CLAUDE.md §6 전제 B).
 * 재사용 캐시 조회와 첨부 텍스트 추출은 백엔드가 끝내고 documents[].text 로 넘겨준다.
 *
 *   clamp(required) → facts → summary → items → legal
 *
 * clamp 만 required 다. 나머지는 하나가 죽어도 나머지 결과로 사용자가 판단을 이어갈 수 있다
 * — 이것이 200 폴백 계약의 논리다(ai-boundary.md §6.4).
 */

import { AiFailure } from '../../http/errors.js';
import type { LlmGateway } from '../../capability/llm/gateway.js';
import type { PromptRegistry } from '../../capability/llm/promptRegistry.js';
import { clampToContext, type ClampedSlice, type SourceDoc } from '../../capability/text/clamp.js';
import { verifyFacts, type Fact } from '../../capability/text/citation.js';
import { degrade, fatal, ok, runPipeline, type Ctx, type Step } from '../kernel.js';

export interface ItemSummaryInput {
  item: Record<string, unknown>;
  documents: SourceDoc[];
  contextWindowTokens: number;
  deep: boolean;
}

interface State {
  input: ItemSummaryInput;
  slices?: ClampedSlice[];
  droppedDocumentIds?: string[];
  facts?: Fact[];
  rejectedFactCount?: number;
  summary?: string;
  items?: Array<Record<string, unknown>>;
  legalAssessment?: Record<string, unknown>;
}

interface Deps {
  llm: LlmGateway;
  prompts: PromptRegistry;
  reviewClauses(slices: ClampedSlice[], ctx: Ctx): Promise<Record<string, unknown>>;
}

const clampStep = (): Step<State> => ({
  name: 'clamp',
  required: true,
  async run(state) {
    const { input } = state;
    if (input.documents.length === 0) {
      return fatal(new AiFailure('BAD_REQUEST', 'documents is empty'));
    }
    const overhead = JSON.stringify(input.item).length;
    const result = clampToContext(input.documents, input.contextWindowTokens, overhead);

    if (result.usedChars === 0) {
      return fatal(new AiFailure('INPUT_TOO_LARGE', `budget=${result.budgetChars}`));
    }
    return ok({ slices: result.slices, droppedDocumentIds: result.droppedDocumentIds });
  },
});

const factsStep = (deps: Deps): Step<State> => ({
  name: 'facts',
  async run(state, ctx) {
    const prompt = deps.prompts.get('item-summary.facts');
    try {
      const raw = await deps.llm.completeJson<{ facts: Fact[] }>(
        prompt,
        { item: state.input.item, slices: state.slices },
        parseFacts,
        ctx.deadline,
      );
      const { accepted, rejected } = verifyFacts(raw.facts, state.input.documents, state.slices ?? []);
      if (rejected.length > 0) {
        ctx.log.warn({ requestId: ctx.requestId, rejected: rejected.length }, 'citations rejected');
      }
      return ok({ facts: accepted, rejectedFactCount: rejected.length });
    } catch (e) {
      return toDegrade(e, 'FACTS', '근거 추출에 실패했습니다.');
    }
  },
});

const summaryStep = (deps: Deps): Step<State> => ({
  name: 'summary',
  async run(state, ctx) {
    const prompt = deps.prompts.get('item-summary.summary');
    try {
      const raw = await deps.llm.completeJson<{ summary: string }>(
        prompt,
        { item: state.input.item, slices: state.slices, facts: state.facts ?? [] },
        (v) => {
          const o = v as { summary?: unknown };
          if (typeof o.summary !== 'string' || o.summary.length === 0) throw new Error('summary missing');
          return { summary: o.summary };
        },
        ctx.deadline,
      );
      return ok({ summary: raw.summary });
    } catch (e) {
      return toDegrade(e, 'SUMMARY', '요약 생성에 실패했습니다.');
    }
  },
});

const itemsStep = (deps: Deps): Step<State> => ({
  name: 'items',
  async run(state, ctx) {
    const prompt = deps.prompts.get('item-summary.items');
    try {
      const raw = await deps.llm.completeJson<{ items: Array<Record<string, unknown>> }>(
        prompt,
        { slices: state.slices },
        (v) => {
          const o = v as { items?: unknown };
          if (!Array.isArray(o.items)) throw new Error('items not array');
          return { items: o.items as Array<Record<string, unknown>> };
        },
        ctx.deadline,
      );
      return ok({ items: raw.items });
    } catch (e) {
      return toDegrade(e, 'ITEMS', '품목 추출에 실패했습니다.');
    }
  },
});

const legalStep = (deps: Deps): Step<State> => ({
  name: 'legal',
  async run(state, ctx) {
    try {
      return ok({ legalAssessment: await deps.reviewClauses(state.slices ?? [], ctx) });
    } catch (e) {
      return toDegrade(e, 'LEGAL', '법령 검토에 실패했습니다.');
    }
  },
});

function toDegrade(e: unknown, code: string, message: string) {
  // 데드라인 초과는 남은 스텝도 어차피 못 돈다. 그래도 부분 결과는 살린다.
  if (e instanceof AiFailure && e.code === 'LLM_TIMEOUT') return degrade<never>('DEADLINE', message);
  return degrade<never>(code, message);
}

function parseFacts(v: unknown): { facts: Fact[] } {
  const o = v as { facts?: unknown };
  if (!Array.isArray(o.facts)) throw new Error('facts not array');
  for (const f of o.facts as Fact[]) {
    if (typeof f?.statement !== 'string') throw new Error('statement missing');
    const ev = f?.evidence;
    if (!ev || typeof ev.documentId !== 'string' || typeof ev.quote !== 'string') {
      throw new Error('evidence missing');
    }
  }
  return { facts: o.facts as Fact[] };
}

export async function runItemSummary(input: ItemSummaryInput, ctx: Ctx, deps: Deps) {
  const steps: Step<State>[] = [
    clampStep(),
    factsStep(deps),
    summaryStep(deps),
    itemsStep(deps),
    legalStep(deps),
  ];
  const outcome = await runPipeline<State>(steps, { input }, ctx);
  const s = outcome.state;

  return {
    promptVersion: ctx.promptVersion,
    degraded: outcome.degraded,
    degradedReasons: outcome.degradedReasons,
    summary: s.summary ?? null,
    facts: s.facts ?? [],
    items: s.items ?? [],
    // documentTags / bidBlockingClauses 는 백엔드 규칙 엔진 소관이다(전제 C).
    documentSignals: { summary: s.summary ?? null, legalAssessment: s.legalAssessment ?? null },
    sourceTrace: {
      steps: ctx.trace.toJSON(),
      droppedDocumentIds: s.droppedDocumentIds ?? [],
      rejectedFactCount: s.rejectedFactCount ?? 0,
    },
    usage: deps.llm.usage,
  };
}
