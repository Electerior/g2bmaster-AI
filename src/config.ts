/**
 * 설정은 기동 시점에 검증한다.
 *
 * 틀린 값으로 그냥 뜨면 첫 요청에서야 터지고, 그때는 백엔드 워커가 이미 작업을
 * 물고 리스를 태우고 있다. 기동을 거부하는 편이 싸다.
 */

export class ConfigError extends Error {
  override readonly name = 'ConfigError';
}

export interface LlmLimits {
  /** 동시 LLM 호출 상한(Principles §4.3 — 상한은 스스로 건다). */
  maxConcurrency: number;
  /** LLM 한 번에 허용하는 절대 상한. 요청 deadlineMs 와 min 을 취한다(§4.1). */
  perCallCapMs: number;
}

export interface Config {
  port: number;
  host: string;
  logLevel: LogLevel;
  llm: LlmLimits;
  /** 모델 컨텍스트 창(토큰). lms.js 자동 탐지를 붙이기 전까지는 설정으로 받는다. */
  contextWindowTokens: number;
}

const LOG_LEVELS = ['fatal', 'error', 'warn', 'info', 'debug', 'trace', 'silent'] as const;
export type LogLevel = (typeof LOG_LEVELS)[number];

type Env = Record<string, string | undefined>;

function intEnv(env: Env, key: string, fallback: number, min: number, max: number): number {
  const raw = env[key];
  if (raw === undefined || raw === '') return fallback;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < min || n > max) {
    throw new ConfigError(`${key}: [${min}, ${max}] 범위의 정수여야 한다. 받은 값=${JSON.stringify(raw)}`);
  }
  return n;
}

function strEnv(env: Env, key: string, fallback: string): string {
  const raw = env[key];
  return raw === undefined || raw === '' ? fallback : raw;
}

function logLevelEnv(env: Env, key: string, fallback: LogLevel): LogLevel {
  const raw = env[key];
  if (raw === undefined || raw === '') return fallback;
  const found = LOG_LEVELS.find((l) => l === raw);
  if (found === undefined) {
    throw new ConfigError(`${key}: ${LOG_LEVELS.join(' | ')} 중 하나여야 한다. 받은 값=${JSON.stringify(raw)}`);
  }
  return found;
}

export function loadConfig(env: Env = process.env): Config {
  return {
    port: intEnv(env, 'PORT', 8_100, 1, 65_535),
    host: strEnv(env, 'HOST', '0.0.0.0'),
    logLevel: logLevelEnv(env, 'LOG_LEVEL', 'info'),
    llm: {
      maxConcurrency: intEnv(env, 'LLM_MAX_CONCURRENCY', 4, 1, 64),
      perCallCapMs: intEnv(env, 'LLM_PER_CALL_CAP_MS', 60_000, 1_000, 600_000),
    },
    contextWindowTokens: intEnv(env, 'CONTEXT_WINDOW_TOKENS', 32_768, 1_024, 2_000_000),
  };
}
