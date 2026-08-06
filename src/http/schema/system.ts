/**
 * 응답 JSON Schema = 계약의 단일 출처(CLAUDE.md §5).
 *
 * Fastify 는 이 스키마로 직렬화까지 한다. 스키마에 없는 필드는 응답에서 사라지므로
 * "코드에는 있는데 백엔드에는 안 가는" 필드가 생기지 않는다.
 * contract 테스트가 이 정의를 스냅샷으로 잡는다(Principles §6.3).
 */

export const errorResponseSchema = {
  type: 'object',
  required: ['code', 'error', 'retryable', 'requestId'],
  additionalProperties: false,
  properties: {
    /** 기계 판독용 실패 분류. 백엔드는 status 가 아니라 이 값으로 판단한다. */
    code: { type: 'string' },
    /** aiError 로 화면에 그대로 렌더링되는 한국어 문구. */
    error: { type: 'string' },
    retryable: { type: 'boolean' },
    retryAfterMs: { type: 'integer' },
    requestId: { type: 'string' },
  },
} as const;

/**
 * GET /api/ai/prompt-version
 *
 * `promptVersion` 은 ai-boundary.md §6.1 의 단일 문자열이고,
 * `versions` 는 엔드포인트별 맵이다(CLAUDE.md §6 전제 F).
 * 전제 F 가 미확인이므로 둘 다 내려보낸다 — 필드 추가는 자유다(Principles §7.1).
 */
export const promptVersionResponseSchema = {
  type: 'object',
  required: ['promptVersion', 'versions'],
  additionalProperties: false,
  properties: {
    promptVersion: { type: ['string', 'null'] },
    versions: {
      type: 'object',
      additionalProperties: { type: 'string' },
    },
  },
} as const;

/**
 * GET /api/ai/capacity — 백엔드 내보내기 ETA 계산용.
 *
 * `llmReachable` 은 정직성 항목이다(Principles §2.3). 슬롯이 비어 있어도
 * 모델에 닿지 못하면 실효 용량은 0 이고, 백엔드는 그걸 알아야 ETA 를 속이지 않는다.
 */
export const capacityResponseSchema = {
  type: 'object',
  required: ['maxConcurrency', 'inFlight', 'availableSlots', 'llmReachable', 'checkedAt'],
  additionalProperties: false,
  properties: {
    maxConcurrency: { type: 'integer' },
    inFlight: { type: 'integer' },
    availableSlots: { type: 'integer' },
    llmReachable: { type: 'boolean' },
    checkedAt: { type: 'string' },
  },
} as const;

/** GET /api/llm/models — 시스템 화면용. */
export const modelsResponseSchema = {
  type: 'object',
  required: ['models', 'reachable', 'checkedAt'],
  additionalProperties: false,
  properties: {
    models: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'reachable'],
        additionalProperties: false,
        properties: {
          id: { type: 'string' },
          contextWindowTokens: { type: 'integer' },
          reachable: { type: 'boolean' },
        },
      },
    },
    reachable: { type: 'boolean' },
    checkedAt: { type: 'string' },
  },
} as const;
