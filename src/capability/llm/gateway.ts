/**
 * LLM 단일 게이트웨이.
 *
 * 모든 모델 호출은 여기를 통과한다. 우회 호출이 하나라도 생기면
 * 프롬프트 버전 자동 파생과 비용 관측이 거짓말이 된다(CLAUDE.md §2-4, Principles §5.3).
 *
 * legacy/llm-worker-pool.js 를 ChatClient 로 주입해 감싼다. 원본은 고치지 않는다.
 */

import { AiFailure } from '../../http/errors.js';
import { Deadline } from '../../pipeline/kernel.js';
import type { RegisteredPrompt } from './promptRegistry.js';

export interface ChatRequest {
  model: string;
  system: string;
  user: string;
  temperature: number;
  topP: number;
  maxTokens: number;
  signal: AbortSignal;
}

export interface ChatResponse {
  text: string;
  promptTokens: number;
  completionTokens: number;
}

/** legacy 어댑터가 구현한다. 풀 소진은 PoolExhausted 로 알린다. */
export interface ChatClient {
  chat(req: ChatRequest): Promise<ChatResponse>;
}

export class PoolExhausted extends Error {
  constructor(readonly retryAfterMs: number) {
    super('llm pool exhausted');
  }
}

export interface Usage {
  promptTokens: number;
  completionTokens: number;
  latencyMs: number;
  calls: number;
}

class Semaphore {
  private active = 0;
  private readonly waiting: Array<() => void> = [];
  constructor(private readonly limit: number) {}

  async acquire(): Promise<() => void> {
    if (this.active >= this.limit) await new Promise<void>((r) => this.waiting.push(r));
    this.active += 1;
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.active -= 1;
      this.waiting.shift()?.();
    };
  }
}

export interface GatewayOptions {
  maxConcurrency: number;
  /** 게이트웨이가 LLM 에 허용하는 최대 시간. 데드라인과 min 을 취한다. */
  perCallCapMs: number;
}

export class LlmGateway {
  private readonly sem: Semaphore;
  readonly usage: Usage = { promptTokens: 0, completionTokens: 0, latencyMs: 0, calls: 0 };

  constructor(
    private readonly client: ChatClient,
    private readonly opts: GatewayOptions,
  ) {
    this.sem = new Semaphore(opts.maxConcurrency);
  }

  /**
   * JSON 응답을 요구하는 호출.
   * 파싱 실패는 같은 요청 안에서 딱 한 번만 재시도한다(재시도는 한 겹, §2-8).
   */
  async completeJson<T>(
    prompt: RegisteredPrompt,
    input: Record<string, unknown>,
    parse: (raw: unknown) => T,
    deadline: Deadline,
  ): Promise<T> {
    let lastError: string | undefined;

    for (let attempt = 0; attempt < 2; attempt += 1) {
      const budget = deadline.budgetFor();
      if (budget <= 0) throw new AiFailure('LLM_TIMEOUT', 'no budget left before llm call');

      const raw = await this.call(prompt, input, Math.min(budget, this.opts.perCallCapMs));
      try {
        return parse(JSON.parse(stripFences(raw)));
      } catch (e) {
        lastError = (e as Error).message;
      }
    }
    throw new AiFailure('LLM_MALFORMED', lastError);
  }

  private async call(prompt: RegisteredPrompt, input: Record<string, unknown>, budgetMs: number): Promise<string> {
    const release = await this.sem.acquire();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), budgetMs);
    const startedAt = Date.now();

    try {
      const res = await this.client.chat({
        model: prompt.model,
        system: prompt.system,
        user: prompt.render(input),
        temperature: prompt.decoding.temperature,
        topP: prompt.decoding.topP,
        maxTokens: prompt.decoding.maxTokens,
        signal: controller.signal,
      });
      this.usage.promptTokens += res.promptTokens;
      this.usage.completionTokens += res.completionTokens;
      this.usage.calls += 1;
      return res.text;
    } catch (e) {
      if (e instanceof PoolExhausted) throw new AiFailure('LLM_UNAVAILABLE', e.message, e.retryAfterMs);
      if (controller.signal.aborted) throw new AiFailure('LLM_TIMEOUT', `aborted after ${budgetMs}ms`);
      throw new AiFailure('LLM_UNAVAILABLE', (e as Error).message);
    } finally {
      clearTimeout(timer);
      this.usage.latencyMs += Date.now() - startedAt;
      release();
    }
  }
}

function stripFences(text: string): string {
  const fenced = /```(?:json)?\s*([\s\S]*?)```/.exec(text);
  return (fenced?.[1] ?? text).trim();
}
