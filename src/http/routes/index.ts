/**
 * 라우트 등록 지점.
 *
 * ai-boundary.md §5 의 11개 표면이 여기서 전부 보여야 한다.
 * 한 곳에 모아 두는 이유는 "우리가 무엇을 소유하는가"를 코드에서 셀 수 있게 하기
 * 위해서다 — contract 테스트가 실제 등록된 경로를 세어 11 인지 확인한다.
 */

import type { FastifyInstance } from 'fastify';

import { notImplementedRoutes } from './notImplemented.js';
import { systemRoutes, type SystemDeps } from './system.js';

export type RouteDeps = SystemDeps;

export async function registerRoutes(app: FastifyInstance, deps: RouteDeps): Promise<void> {
  await systemRoutes(app, deps);
  await notImplementedRoutes(app);

  // itemSummaryRoute 는 M3 에서 등록한다. 파이프라인 구현은 이미 트리에 있고
  // golden 테스트가 덮고 있지만, 호출할 ChatClient 가 없으므로 표면은 아직 열지 않는다.
  // 그때 /api/item-summary 를 PENDING_ENDPOINTS 에서 빼고 여기에 붙인다.
}
