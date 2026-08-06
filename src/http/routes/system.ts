/**
 * 시스템 조회 3종. LLM 없이도 정직하게 답할 수 있는 표면이라 M0 에서 먼저 연다.
 *
 * 라우트는 얇은 어댑터다 — 값을 만드는 책임은 capability 쪽에 있다.
 */

import type { FastifyInstance } from 'fastify';

import type { PromptRegistry } from '../../capability/llm/promptRegistry.js';
import type { LlmStatusSource } from '../../capability/llm/status.js';
import {
  capacityResponseSchema,
  errorResponseSchema,
  modelsResponseSchema,
  promptVersionResponseSchema,
} from '../schema/system.js';

export interface SystemDeps {
  prompts: PromptRegistry;
  llmStatus: LlmStatusSource;
}

/**
 * 단일 문자열 `promptVersion` 이 가리키는 대상.
 * ai-boundary.md §6.1 의 예시 값이 item-summary 것이었으므로 그 의미를 유지한다.
 */
const LEGACY_PROMPT_VERSION_ID = 'item-summary';

export async function systemRoutes(app: FastifyInstance, deps: SystemDeps): Promise<void> {
  app.get(
    '/api/ai/prompt-version',
    { schema: { response: { 200: promptVersionResponseSchema, 500: errorResponseSchema } } },
    async () => {
      const versions = deps.prompts.versionMap();
      return {
        // 등록된 프롬프트가 없으면 null 이다. "버전이 없다"를 빈 문자열로 위장하면
        // 백엔드가 그걸 재사용 키에 넣고 서로 다른 상태를 같은 키로 묶는다.
        promptVersion: versions[LEGACY_PROMPT_VERSION_ID] ?? null,
        versions,
      };
    },
  );

  app.get(
    '/api/ai/capacity',
    { schema: { response: { 200: capacityResponseSchema, 500: errorResponseSchema } } },
    async () => deps.llmStatus.capacity(),
  );

  app.get(
    '/api/llm/models',
    { schema: { response: { 200: modelsResponseSchema, 500: errorResponseSchema } } },
    async () => deps.llmStatus.models(),
  );
}
