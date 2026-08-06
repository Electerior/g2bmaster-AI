/**
 * 아직 구현되지 않은 8개 표면의 정직한 503.
 *
 * M0 가 존재하는 이유가 이것이다(CLAUDE.md §5): 정상 경로보다 **폴백 계약을 먼저**
 * 검증한다. 백엔드가 `AiUnavailableException` → 200 폴백을 제대로 만드는지,
 * 그 폴백이 캐시에 눌러앉지 않는지를 LLM 없이 확인할 수 있다.
 *
 * 라우트가 없으면 404 가 나가고, 404 는 "배포가 잘못됐다"와 "아직 안 만들었다"를
 * 구분하지 못한다. 그래서 침묵이 아니라 분류된 실패를 내려보낸다.
 */

import type { FastifyInstance } from 'fastify';

import { AiFailure, toHttp } from '../errors.js';
import { resolveRequestId } from '../requestId.js';
import { errorResponseSchema } from '../schema/system.js';

export interface PendingEndpoint {
  readonly path: string;
  /** 이 표면이 열리는 마일스톤(CLAUDE.md §5). 로그에만 싣는다. */
  readonly milestone: string;
}

/**
 * ai-boundary.md §5 의 11개 중 POST 8개.
 * GET 3개는 systemRoutes 가 실제로 구현한다.
 */
export const PENDING_ENDPOINTS: readonly PendingEndpoint[] = Object.freeze([
  { path: '/api/embed', milestone: 'M1' },
  { path: '/api/bid-summary', milestone: 'M2' },
  { path: '/api/item-summary', milestone: 'M3' },
  { path: '/api/price/resolve', milestone: 'M4' },
  { path: '/api/price/url', milestone: 'M4' },
  { path: '/api/legal/review-clauses', milestone: 'M5' },
  { path: '/api/legal/outreach-draft', milestone: 'M5' },
  { path: '/api/pledge/revision-workflow', milestone: 'M5' },
]);

export async function notImplementedRoutes(app: FastifyInstance): Promise<void> {
  for (const endpoint of PENDING_ENDPOINTS) {
    app.post(
      endpoint.path,
      {
        // 본문 스키마를 걸지 않는다. 아직 계약이 확정되지 않은 표면에 검증을 걸면
        // 백엔드가 "형식이 틀렸다"(400)와 "아직 없다"(503)를 헷갈린다.
        schema: { response: { 503: errorResponseSchema } },
      },
      async (req, reply) => {
        const requestId = resolveRequestId(req);
        const { status, body } = toHttp(new AiFailure('NOT_IMPLEMENTED', `pending ${endpoint.milestone}`), requestId);

        req.log.info({ requestId, path: endpoint.path, milestone: endpoint.milestone }, 'endpoint not implemented yet');
        // status 는 분류표에서 온다. 위 스키마가 503 하나만 선언했으므로 타입을 좁혀 준다.
        // 분류표에서 NOT_IMPLEMENTED 의 status 가 바뀌면 contract 테스트가 먼저 깨진다.
        return reply.code(status as 503).send(body);
      },
    );
  }
}
