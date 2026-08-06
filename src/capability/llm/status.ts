/**
 * LLM 가용성 보고.
 *
 * `GET /api/ai/capacity` 와 `GET /api/llm/models` 가 읽는다.
 * 백엔드는 이 값으로 내보내기 ETA 를 계산하므로, 낙관적으로 답하면
 * 사용자에게 지키지 못할 시간을 약속하게 된다(Principles §2.3).
 */

import type { LlmLimits } from '../../config.js';

export interface ModelInfo {
  id: string;
  contextWindowTokens?: number;
  reachable: boolean;
}

export interface ModelsSnapshot {
  models: ModelInfo[];
  /** 하나라도 닿으면 true. */
  reachable: boolean;
  checkedAt: string;
}

export interface CapacitySnapshot {
  maxConcurrency: number;
  inFlight: number;
  availableSlots: number;
  /** false 면 슬롯이 비어 있어도 실효 용량은 0 이다. */
  llmReachable: boolean;
  checkedAt: string;
}

export interface LlmStatusSource {
  capacity(): CapacitySnapshot;
  models(): Promise<ModelsSnapshot>;
}

/**
 * M0 용 보고자. 모델 백엔드가 아직 배선되지 않았다.
 *
 * 슬롯 수는 설정대로 보고하되 `llmReachable: false`, `availableSlots: 0` 을 함께 보낸다.
 * "동시성 4" 만 보고 백엔드가 네 건을 밀어넣으면 전부 503 을 받는다 —
 * 그건 우리가 거짓말을 한 것이다.
 *
 * M1 에서 lms.js 어댑터가 붙으면 이 구현을 실제 탐지로 교체한다.
 */
export function unwiredLlmStatus(limits: LlmLimits, now: () => Date = () => new Date()): LlmStatusSource {
  return {
    capacity(): CapacitySnapshot {
      return {
        maxConcurrency: limits.maxConcurrency,
        inFlight: 0,
        availableSlots: 0,
        llmReachable: false,
        checkedAt: now().toISOString(),
      };
    },
    async models(): Promise<ModelsSnapshot> {
      return { models: [], reachable: false, checkedAt: now().toISOString() };
    },
  };
}
