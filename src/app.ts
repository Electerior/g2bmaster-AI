/**
 * 앱 조립. listen 은 하지 않는다 — server.ts 가 한다.
 *
 * 나눠 둔 이유는 테스트 때문이다. contract 테스트는 포트를 열지 않고
 * `app.inject()` 로 실제 라우팅·검증·직렬화를 통과시킨다.
 */

import Fastify, { type FastifyInstance } from 'fastify';

import { PromptRegistry } from './capability/llm/promptRegistry.js';
import { unwiredLlmStatus, type LlmStatusSource } from './capability/llm/status.js';
import { loadConfig, type Config } from './config.js';
import { AiFailure, FAILURES, toHttp } from './http/errors.js';
import { resolveRequestId } from './http/requestId.js';
import { registerRoutes } from './http/routes/index.js';

/**
 * 요청 본문 상한(Principles §4.3 — 상한은 스스로 건다).
 *
 * 백엔드가 첨부에서 뽑은 원문 텍스트를 통째로 실어 보내므로 Fastify 기본값 1MB 로는
 * 모자란다. 반대로 무제한이면 검색 팬아웃 한 번에 우리가 먼저 무너진다.
 */
const BODY_LIMIT_BYTES = 16 * 1024 * 1024;

export interface AppOverrides {
  config?: Config;
  /** 비워 두면 빈 레지스트리. M0 에는 아직 등록된 프롬프트가 없다. */
  prompts?: PromptRegistry;
  llmStatus?: LlmStatusSource;
}

export async function buildApp(overrides: AppOverrides = {}): Promise<FastifyInstance> {
  const config = overrides.config ?? loadConfig();
  const prompts = overrides.prompts ?? new PromptRegistry();
  const llmStatus = overrides.llmStatus ?? unwiredLlmStatus(config.llm);

  const app = Fastify({
    logger: { level: config.logLevel },
    bodyLimit: BODY_LIMIT_BYTES,
    // 백엔드가 헤더로 준 요청 ID 를 Fastify 로그에도 그대로 쓴다.
    // 본문의 requestId 는 이 시점에 아직 파싱 전이라 resolveRequestId 가 따로 본다.
    genReqId: (req) => {
      const header = req.headers['x-request-id'];
      return typeof header === 'string' && header.length > 0 ? header : globalThis.crypto.randomUUID();
    },
  });

  /**
   * 실패 응답의 모양을 한 곳으로 모은다.
   *
   * 이걸 안 걸면 스키마 검증 실패는 Fastify 기본 `{statusCode, error, message}` 로
   * 나가고, 백엔드는 code/retryable 이 없는 본문을 만나 분류에 실패한다.
   */
  app.setErrorHandler((err: unknown, req, reply) => {
    const requestId = resolveRequestId(req);
    // Fastify 가 4xx 로 판정한 것 = 호출자 쪽 문제 = 재시도해도 같다.
    const failure = isCallerError(err) ? new AiFailure('BAD_REQUEST', err.message) : err;
    const { status, body } = toHttp(failure, requestId);

    if (failure instanceof AiFailure) {
      req.log.warn({ requestId, code: failure.code, detail: failure.detail }, 'request failed');
    } else {
      // 분류되지 않은 예외 = 우리 버그. 스택을 남긴다.
      req.log.error({ requestId, err }, 'unclassified error');
    }
    return reply.code(status).send(body);
  });

  /**
   * 우리가 소유하지 않는 경로. 분류표에 없는 상황이라 직접 조립한다.
   * 호출자 쪽 문제이고 재시도해도 달라지지 않으므로 retryable=false 다.
   */
  app.setNotFoundHandler((req, reply) => {
    const requestId = resolveRequestId(req);
    req.log.warn({ requestId, method: req.method, url: req.url }, 'unknown route');
    return reply.code(404).send({
      code: 'BAD_REQUEST',
      error: FAILURES.BAD_REQUEST.message,
      retryable: false,
      requestId,
    });
  });

  await registerRoutes(app, { prompts, llmStatus });
  return app;
}

/**
 * 호출자 쪽 잘못으로 Fastify 가 이미 판정한 실패인가.
 *
 * 두 갈래를 함께 본다:
 *  - `validation` — 요청 스키마 검증 실패.
 *  - `statusCode` 4xx — 깨진 JSON 본문, 본문 상한 초과, 지원하지 않는 content-type 등.
 *    이쪽을 놓치면 "본문에 오타가 있다" 가 INTERNAL(500, 재시도 가능)로 분류되어
 *    백엔드가 절대 성공할 수 없는 요청을 재시도 예산이 다할 때까지 반복한다.
 *
 * 핸들러가 받는 값을 `unknown` 으로 두는 이유는 정직해서다 — 라우트 안에서는
 * 무엇이든 throw 될 수 있고, `Error` 가 아닌 값이 오는 경우도 실제로 있다.
 */
interface CallerError {
  message: string;
}

function isCallerError(err: unknown): err is CallerError {
  if (typeof err !== 'object' || err === null) return false;
  const candidate = err as { validation?: unknown; statusCode?: unknown };

  if (candidate.validation !== undefined) return true;
  return typeof candidate.statusCode === 'number' && candidate.statusCode >= 400 && candidate.statusCode < 500;
}
