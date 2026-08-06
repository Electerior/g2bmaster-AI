/**
 * ============================================================================
 * WHAT  — LLM 게이트웨이의 실패 처리. 재시도 겹수, 중단, 풀 소진, 동시성 상한.
 *
 * WHY   — 이 파일이 지키는 계약 셋:
 *         (1) CLAUDE.md §2-8 "재시도는 한 겹만." 백엔드에 이미 재시도가 있다.
 *             우리가 몰래 한 겹 더 두면 백엔드의 ETA 계산과 비용 예측이 깨지고,
 *             장애 시 부하가 곱으로 늘어난다.
 *         (2) §2-7 데드라인 부등식. 우리가 백엔드 리스보다 늦게 끝나면 다른 워커가
 *             같은 작업을 다시 집어 LLM 비용이 두 배가 된다.
 *         (3) Principles §4.3 "상한은 스스로 건다." 검색 팬아웃이 수십 건을 밀어넣어도
 *             워커 풀이 아니라 우리가 먼저 무너지면 안 된다.
 *
 * WHERE — src/capability/llm/gateway.ts (LlmGateway.completeJson)
 *
 * HOW   — ScriptedChatClient 로 "모델이 이렇게 굴 때" 를 대본으로 만든다.
 *         호출 횟수(client.requests.length)가 핵심 관측점이다 — 재시도 겹수는
 *         반환값이 아니라 **호출 횟수로만** 확인할 수 있기 때문이다.
 * ============================================================================
 */

import { describe, expect, it } from 'vitest';

import { LlmGateway, PoolExhausted, type ChatRequest, type ChatResponse } from '../../src/capability/llm/gateway.js';
import { PromptRegistry, type RegisteredPrompt } from '../../src/capability/llm/promptRegistry.js';
import { Deadline } from '../../src/pipeline/kernel.js';
import { ScriptedChatClient, chatResponse, deferred, promptFixture } from '../helpers/fixtures.js';

/** 게이트웨이에 넘길 등록된 프롬프트. */
function prompt(): RegisteredPrompt {
  return new PromptRegistry().register('test', promptFixture());
}

/** 파싱기. 계약을 지키지 않는 응답은 여기서 던진다 — 게이트웨이가 재시도할 신호다. */
function parseX(raw: unknown): { x: number } {
  const o = raw as { x?: unknown };
  if (typeof o.x !== 'number') throw new Error('x 가 숫자가 아니다');
  return { x: o.x };
}

/** 이벤트 루프를 한 바퀴 돌려 대기 중인 작업을 소화시킨다. */
const tick = (ms = 5): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

describe('정상 경로', () => {
  it('JSON 을 파싱해 돌려주고 사용량을 누적한다', async () => {
    // WHY: usage 는 응답에 실려 나가는 관측값이다. 게이트웨이를 우회하는 호출이
    //      생기면 이 숫자가 거짓말이 된다(Principles §5.3).
    const client = new ScriptedChatClient([async () => chatResponse('{"x":7}')]);
    const gateway = new LlmGateway(client, { maxConcurrency: 2, perCallCapMs: 60_000 });

    const result = await gateway.completeJson(prompt(), { a: 1 }, parseX, Deadline.in(60_000));

    expect(result).toEqual({ x: 7 });
    expect(client.requests).toHaveLength(1);
    expect(gateway.usage).toMatchObject({ calls: 1, promptTokens: 10, completionTokens: 5 });
    expect(gateway.usage.latencyMs).toBeGreaterThanOrEqual(0);
  });

  it('```json 펜스로 감싼 응답도 파싱한다', async () => {
    // WHY: 지시를 줘도 모델은 펜스를 붙인다. 이걸 못 벗기면 정상 응답이 전부
    //      LLM_MALFORMED 가 되어 "모델이 이상하다" 는 오진을 부른다.
    const client = new ScriptedChatClient([async () => chatResponse('```json\n{"x":1}\n```')]);
    const gateway = new LlmGateway(client, { maxConcurrency: 1, perCallCapMs: 60_000 });

    await expect(gateway.completeJson(prompt(), {}, parseX, Deadline.in(60_000))).resolves.toEqual({ x: 1 });
  });

  it('프롬프트의 모델·디코딩 파라미터를 그대로 클라이언트에 넘긴다', async () => {
    // WHY: 여기서 값이 갈리면 프롬프트 버전(해시)과 실제 호출이 달라진다 —
    //      버전 자동 파생이 거짓말이 되는 정확한 경로다.
    const client = new ScriptedChatClient([async () => chatResponse('{"x":1}')]);
    const gateway = new LlmGateway(client, { maxConcurrency: 1, perCallCapMs: 60_000 });
    const p = prompt();

    await gateway.completeJson(p, { 품목: '서버' }, parseX, Deadline.in(60_000));

    expect(client.requests[0]).toMatchObject({
      model: p.model,
      system: p.system,
      temperature: p.decoding.temperature,
      topP: p.decoding.topP,
      maxTokens: p.decoding.maxTokens,
    });
    expect(client.requests[0]?.user).toContain('서버');
  });
});

describe('재시도는 한 겹만 (CLAUDE.md §2-8)', () => {
  it('깨진 JSON 은 딱 한 번 더 시도한다 — 두 번째가 성공하면 살린다', async () => {
    // WHY: JSON 파싱 실패는 같은 요청 안에서 값싸게 회복 가능한 것에 해당한다.
    //      이것만 재시도 대상이고, 그 이상은 실패로 올린다.
    const client = new ScriptedChatClient([
      async () => chatResponse('여기 있습니다: {x: 1'), // 잘린 JSON
      async () => chatResponse('{"x":1}'),
    ]);
    const gateway = new LlmGateway(client, { maxConcurrency: 1, perCallCapMs: 60_000 });

    await expect(gateway.completeJson(prompt(), {}, parseX, Deadline.in(60_000))).resolves.toEqual({ x: 1 });
    expect(client.requests).toHaveLength(2);
  });

  it('두 번 다 깨지면 LLM_MALFORMED 이고 호출은 정확히 2회에서 멈춘다', async () => {
    // HOW: 호출 횟수가 2를 넘으면 재시도가 두 겹이 된 것이다. 이 숫자가 이 테스트의
    //      전부다 — 반환값만 봐서는 몇 번 불렀는지 알 수 없다.
    const client = new ScriptedChatClient([async () => chatResponse('완전히 깨진 응답')]);
    const gateway = new LlmGateway(client, { maxConcurrency: 1, perCallCapMs: 60_000 });

    await expect(gateway.completeJson(prompt(), {}, parseX, Deadline.in(60_000))).rejects.toMatchObject({
      code: 'LLM_MALFORMED',
    });
    expect(client.requests).toHaveLength(2);
  });

  it('스키마는 맞는데 계약을 어긴 응답도 같은 경로로 처리한다', async () => {
    // 유효한 JSON 이지만 x 가 문자열 → 파서가 던진다 → 재시도 후 LLM_MALFORMED.
    const client = new ScriptedChatClient([async () => chatResponse('{"x":"일곱"}')]);
    const gateway = new LlmGateway(client, { maxConcurrency: 1, perCallCapMs: 60_000 });

    await expect(gateway.completeJson(prompt(), {}, parseX, Deadline.in(60_000))).rejects.toMatchObject({
      code: 'LLM_MALFORMED',
    });
    expect(client.requests).toHaveLength(2);
  });
});

describe('풀 소진 — 재시도 가치가 있는 실패', () => {
  it('PoolExhausted 는 LLM_UNAVAILABLE 로 바꾸고 retryAfterMs 를 보존한다', async () => {
    // WHY: 이 값은 백엔드가 언제 다시 집을지 정하는 근거다. 잃어버리면 백엔드는
    //      즉시 재시도하고, 이미 소진된 풀을 계속 두드린다.
    const client = new ScriptedChatClient([
      async () => {
        throw new PoolExhausted(3_000);
      },
    ]);
    const gateway = new LlmGateway(client, { maxConcurrency: 1, perCallCapMs: 60_000 });

    await expect(gateway.completeJson(prompt(), {}, parseX, Deadline.in(60_000))).rejects.toMatchObject({
      code: 'LLM_UNAVAILABLE',
      retryAfterMs: 3_000,
    });
    // 풀 소진은 파싱 실패가 아니다 — 재시도 루프를 타지 않고 바로 올라간다.
    expect(client.requests).toHaveLength(1);
  });

  it('그 밖의 클라이언트 예외도 LLM_UNAVAILABLE 로 분류한다', async () => {
    const client = new ScriptedChatClient([
      async () => {
        throw new Error('ECONNREFUSED');
      },
    ]);
    const gateway = new LlmGateway(client, { maxConcurrency: 1, perCallCapMs: 60_000 });

    await expect(gateway.completeJson(prompt(), {}, parseX, Deadline.in(60_000))).rejects.toMatchObject({
      code: 'LLM_UNAVAILABLE',
    });
  });
});

describe('시간 상한 (CLAUDE.md §2-7)', () => {
  /** 중단될 때까지 영원히 기다리는 모델. 실제 장애의 가장 흔한 모습이다. */
  const hanging = async (req: ChatRequest): Promise<ChatResponse> =>
    new Promise<ChatResponse>((_resolve, reject) => {
      req.signal.addEventListener('abort', () => reject(new Error('테스트에서 중단됨')));
    });

  it('남은 예산이 없으면 모델을 부르지도 않는다', async () => {
    // WHY: 부를 시간이 없는데 부르면 그 호출은 100% 낭비다. 토큰만 쓰고 버린다.
    // HOW: budgetFor 의 기본 여유가 1000ms 이므로, 남은 시간이 그보다 적으면 예산은 0 이다.
    const client = new ScriptedChatClient([async () => chatResponse('{"x":1}')]);
    const gateway = new LlmGateway(client, { maxConcurrency: 1, perCallCapMs: 60_000 });

    await expect(gateway.completeJson(prompt(), {}, parseX, Deadline.in(500))).rejects.toMatchObject({
      code: 'LLM_TIMEOUT',
    });
    expect(client.requests).toHaveLength(0);
  });

  it('perCallCapMs 를 넘기면 중단하고 LLM_TIMEOUT 으로 분류한다', async () => {
    // 데드라인은 넉넉한데 게이트웨이 자체 상한이 작은 경우 → 상한이 이긴다.
    const client = new ScriptedChatClient([hanging]);
    const gateway = new LlmGateway(client, { maxConcurrency: 1, perCallCapMs: 30 });

    await expect(gateway.completeJson(prompt(), {}, parseX, Deadline.in(60_000))).rejects.toMatchObject({
      code: 'LLM_TIMEOUT',
    });
    expect(client.requests[0]?.signal.aborted).toBe(true);
  });

  it('데드라인이 상한보다 짧으면 데드라인이 이긴다 (둘 중 작은 쪽)', async () => {
    // WHY: 이 min 이 §2-7 부등식의 집행 지점이다. 상한만 보면 백엔드 리스를 넘긴다.
    const client = new ScriptedChatClient([hanging]);
    const gateway = new LlmGateway(client, { maxConcurrency: 1, perCallCapMs: 60_000 });

    // 남은 시간 1050ms - 여유 1000ms = 예산 약 50ms
    await expect(gateway.completeJson(prompt(), {}, parseX, Deadline.in(1_050))).rejects.toMatchObject({
      code: 'LLM_TIMEOUT',
    });
    expect(client.requests[0]?.signal.aborted).toBe(true);
  });
});

describe('동시성 상한 (Principles §4.3)', () => {
  it('maxConcurrency 를 넘는 호출은 앞 호출이 끝날 때까지 대기한다', async () => {
    // WHY: 상한이 없으면 검색 팬아웃 한 번에 수십 개의 요청이 동시에 모델로 간다.
    //      워커 풀이 아니라 우리가 먼저 무너진다.
    // HOW: 응답을 테스트가 직접 해소(deferred)해서 첫 호출을 붙잡아 둔 뒤,
    //      두 번째 호출이 클라이언트까지 내려왔는지 호출 횟수로 확인한다.
    const first = deferred<ChatResponse>();
    const second = deferred<ChatResponse>();
    const client = new ScriptedChatClient([async () => first.promise, async () => second.promise]);
    const gateway = new LlmGateway(client, { maxConcurrency: 1, perCallCapMs: 60_000 });

    const p1 = gateway.completeJson(prompt(), {}, parseX, Deadline.in(60_000));
    const p2 = gateway.completeJson(prompt(), {}, parseX, Deadline.in(60_000));

    await tick();
    expect(client.requests).toHaveLength(1); // 두 번째는 아직 세마포어 밖에서 대기 중

    first.resolve(chatResponse('{"x":1}'));
    await expect(p1).resolves.toEqual({ x: 1 });

    await tick();
    expect(client.requests).toHaveLength(2); // 슬롯이 비자마자 들어갔다

    second.resolve(chatResponse('{"x":2}'));
    await expect(p2).resolves.toEqual({ x: 2 });
  });

  it('호출이 실패해도 슬롯은 반드시 반납된다', async () => {
    // WHY: 예외 경로에서 반납을 빠뜨리면 장애 때 슬롯이 하나씩 영구히 사라지고,
    //      결국 아무 요청도 못 받는 상태로 조용히 굳는다. finally 가 그래서 있다.
    const client = new ScriptedChatClient([
      async () => {
        throw new Error('폭발');
      },
      async () => chatResponse('{"x":9}'),
    ]);
    const gateway = new LlmGateway(client, { maxConcurrency: 1, perCallCapMs: 60_000 });

    await expect(gateway.completeJson(prompt(), {}, parseX, Deadline.in(60_000))).rejects.toBeDefined();
    // 슬롯이 새어 나갔다면 이 호출은 영원히 매달린다.
    await expect(gateway.completeJson(prompt(), {}, parseX, Deadline.in(60_000))).resolves.toEqual({ x: 9 });
  });
});
