/**
 * 테스트 공용 픽스처.
 *
 * WHAT  — 여러 테스트가 공유하는 최소한의 조립 도구(설정, Ctx, 프롬프트, 가짜 ChatClient).
 * WHY   — 같은 조립 코드를 파일마다 복사하면 한쪽만 고쳐지고 테스트끼리 전제가 갈린다.
 * WHERE — test/ 전역. 프로덕션 코드는 이 파일을 절대 import 하지 않는다.
 * HOW   — 전부 순수하거나 주입 가능한 형태로 만든다. 실제 네트워크·타이머·모델은 없다.
 */

import type { ChatClient, ChatRequest, ChatResponse } from '../../src/capability/llm/gateway.js';
import type { PromptDef } from '../../src/capability/llm/promptRegistry.js';
import type { Config } from '../../src/config.js';
import { Deadline, Trace, type Ctx } from '../../src/pipeline/kernel.js';

/**
 * 테스트용 설정.
 * logLevel 을 silent 로 두는 이유는 취향이 아니라 신호 대 잡음이다 —
 * 폴트 주입 테스트는 의도적으로 실패를 만들기 때문에 로그가 실제 실패를 덮어 버린다.
 */
export const TEST_CONFIG: Config = Object.freeze({
  port: 0,
  host: '127.0.0.1',
  logLevel: 'silent',
  llm: { maxConcurrency: 4, perCallCapMs: 60_000 },
  contextWindowTokens: 32_768,
});

/** 테스트가 로그를 검증할 수 있도록 기록만 하는 로거. */
export interface RecordedLog {
  level: 'info' | 'warn';
  payload: object;
  message: string | undefined;
}

export function recordingLog(): { log: Ctx['log']; entries: RecordedLog[] } {
  const entries: RecordedLog[] = [];
  return {
    entries,
    log: {
      info: (payload: object, message?: string) => entries.push({ level: 'info', payload, message }),
      warn: (payload: object, message?: string) => entries.push({ level: 'warn', payload, message }),
    },
  };
}

/**
 * 파이프라인 Ctx 조립.
 * deadlineMs 를 인자로 받는 이유는 데드라인이 이 저장소에서 가장 자주 검증해야 하는
 * 축이기 때문이다(Principles §4.1 — 데드라인은 호출자가 준다).
 */
export function makeCtx(deadlineMs: number): Ctx & { entries: RecordedLog[] } {
  const { log, entries } = recordingLog();
  return {
    requestId: 'test-request-id',
    deadline: Deadline.in(deadlineMs),
    trace: new Trace(),
    promptVersion: 'test-prompt-version',
    log,
    entries,
  };
}

/**
 * 프롬프트 정의 픽스처.
 *
 * 주의: `render` 는 함수이고 promptRegistry 는 `render.toString()` 을 해시 재료로 쓴다.
 * 따라서 이 함수의 **소스 텍스트**가 바뀌면 버전이 바뀐다. 테스트에서 임의로
 * 공백을 고치면 버전 비교 테스트가 흔들린다 — 고칠 일이 있으면 의도적으로 고쳐라.
 */
export function promptFixture(overrides: Partial<PromptDef> = {}): PromptDef {
  return {
    label: 'item-summary',
    model: 'qwen2.5-32b-instruct',
    decoding: { temperature: 0.2, topP: 0.9, maxTokens: 2_048 },
    system: '너는 조달 공고 분석기다. 반드시 JSON 으로만 답한다.',
    render: (input: Record<string, unknown>) => JSON.stringify(input),
    postprocessRev: 1,
    ...overrides,
  };
}

/** 지연 해소가 가능한 약속. 동시성·중단 테스트에 쓴다. */
export interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
}

export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

export function chatResponse(text: string): ChatResponse {
  return { text, promptTokens: 10, completionTokens: 5 };
}

/**
 * 대본대로 응답하는 가짜 ChatClient.
 *
 * WHY  — 게이트웨이의 재시도·중단·상한은 "모델이 이렇게 굴 때 우리가 어떻게 하는가"의
 *        문제다. 실제 모델로는 그 상황을 재현할 수 없다(Principles §6.2 실패 주입).
 * HOW  — n 번째 호출에 script[n] 을 쓴다. 대본이 떨어지면 마지막 항목을 반복한다.
 */
export class ScriptedChatClient implements ChatClient {
  readonly requests: ChatRequest[] = [];

  constructor(private readonly script: ReadonlyArray<(req: ChatRequest) => Promise<ChatResponse>>) {}

  async chat(req: ChatRequest): Promise<ChatResponse> {
    this.requests.push(req);
    const step = this.script[this.requests.length - 1] ?? this.script.at(-1);
    if (step === undefined) throw new Error('ScriptedChatClient: 대본이 비어 있다');
    return step(req);
  }
}
