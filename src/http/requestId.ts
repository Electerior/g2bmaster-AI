import { randomUUID } from 'node:crypto';
import type { FastifyRequest } from 'fastify';

/**
 * 백엔드가 준 requestId 를 최우선으로 쓴다.
 *
 * 이 값은 두 저장소의 로그를 잇는 유일한 실이다. 여기서 새로 만들어 버리면
 * 백엔드 로그의 작업 하나와 우리 로그의 요청 하나를 이어 볼 방법이 사라진다.
 */
export function resolveRequestId(req: FastifyRequest): string {
  const fromBody = (req.body as { requestId?: unknown } | undefined)?.requestId;
  if (typeof fromBody === 'string' && fromBody.length > 0) return fromBody;

  const fromHeader = req.headers['x-request-id'];
  if (typeof fromHeader === 'string' && fromHeader.length > 0) return fromHeader;

  return randomUUID();
}
