/**
 * 라우트는 얇은 어댑터다. 로직을 두지 않는다.
 * 하는 일: 스키마 검증 → Ctx 조립 → 파이프라인 호출 → 예외를 HTTP 로 매핑.
 *
 * 응답 스키마는 계약의 단일 출처이며 contract 테스트가 이 정의를 스냅샷으로 잡는다.
 */

import type { FastifyInstance, FastifyRequest } from 'fastify';
import { randomUUID } from 'node:crypto';

import { AiFailure, toHttp } from '../errors.js';
import { Deadline, Trace, type Ctx } from '../../pipeline/kernel.js';
import { runItemSummary } from '../../pipeline/item-summary/index.js';

const requestSchema = {
  type: 'object',
  required: ['documents'],
  additionalProperties: false,
  properties: {
    requestId: { type: 'string' },
    deadlineMs: { type: 'integer', minimum: 1_000, maximum: 600_000, default: 90_000 },
    options: {
      type: 'object',
      additionalProperties: false,
      properties: { deep: { type: 'boolean', default: false } },
      default: {},
    },
    item: { type: 'object', additionalProperties: true, default: {} },
    documents: {
      type: 'array',
      minItems: 1,
      maxItems: 100,
      items: {
        type: 'object',
        required: ['documentId', 'name', 'text'],
        additionalProperties: false,
        properties: {
          documentId: { type: 'string', minLength: 1 },
          name: { type: 'string' },
          text: { type: 'string' },
        },
      },
    },
  },
} as const;

type Body = {
  requestId?: string;
  deadlineMs: number;
  options: { deep: boolean };
  item: Record<string, unknown>;
  documents: Array<{ documentId: string; name: string; text: string }>;
};

export async function itemSummaryRoute(app: FastifyInstance, deps: Parameters<typeof runItemSummary>[2] & {
  contextWindowTokens: number;
  promptVersionFor(id: string): string;
}): Promise<void> {
  app.post('/api/item-summary', { schema: { body: requestSchema } }, async (req: FastifyRequest, reply) => {
    const body = req.body as Body;
    const requestId = body.requestId ?? randomUUID();

    const ctx: Ctx = {
      requestId,
      // 백엔드 timeout 보다 먼저 끝내기 위해 자체 여유를 뺀다(CLAUDE.md §2-7).
      deadline: Deadline.in(Math.max(1_000, body.deadlineMs - 2_000)),
      trace: new Trace(),
      promptVersion: deps.promptVersionFor('item-summary'),
      log: req.log,
    };

    try {
      const result = await runItemSummary(
        {
          item: body.item,
          documents: body.documents,
          contextWindowTokens: deps.contextWindowTokens,
          deep: body.options.deep,
        },
        ctx,
        deps,
      );
      return reply.code(200).send(result);
    } catch (e) {
      const { status, body: errBody } = toHttp(e, requestId);
      if (e instanceof AiFailure) {
        req.log.warn({ requestId, code: e.code, detail: e.detail }, 'item-summary failed');
      } else {
        req.log.error({ requestId, err: e }, 'item-summary crashed');
      }
      return reply.code(status).send(errBody);
    }
  });
}
