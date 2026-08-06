/**
 * ============================================================================
 * WHAT  — 파이프라인 커널의 실패 갈림길. ok / degrade / fatal / 데드라인 초과.
 *
 * WHY   — CLAUDE.md §2-2: 스텝은 예외를 던지지 않고 세 결과 중 하나를 **선택**한다.
 *         이 선택이 사라지면 "계속 갈 수 있음" 과 "여기서 끝" 의 구분이 없어져
 *         전부 500 이 되고, 백엔드는 부분 결과를 살릴 기회를 잃는다.
 *         Principles §6.2: "실패 주입이 정상 경로만큼 중요하다. 이것이 폴백 계약의
 *         유일한 자동 검증 수단이다."
 *
 * WHERE — src/pipeline/kernel.ts (runPipeline, Deadline, Trace)
 *
 * HOW   — 진짜 LLM 대신 결과를 **직접 고르는** 가짜 스텝을 넣는다.
 *         스텝이 무엇을 반환하느냐만 바꿔 가며 커널의 분기를 전수로 훑는다.
 *         시간 관련 케이스는 아주 짧은 실제 대기(수십 ms)를 쓴다 — 가짜 타이머로는
 *         "await 도중에 데드라인이 지나간다" 를 재현하기 어렵기 때문이다.
 * ============================================================================
 */

import { describe, expect, it } from 'vitest';

import { AiFailure } from '../../src/http/errors.js';
import { Deadline, degrade, fatal, ok, runPipeline, type Step } from '../../src/pipeline/kernel.js';
import { makeCtx } from '../helpers/fixtures.js';

/** 테스트용 파이프라인 상태. 각 스텝이 자기 칸만 채운다. */
interface State {
  first?: string;
  second?: string;
  third?: string;
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/** 성공만 하는 스텝. */
const okStep = (name: string, patch: Partial<State>, delayMs = 0): Step<State> => ({
  name,
  async run() {
    if (delayMs > 0) await sleep(delayMs);
    return ok(patch);
  },
});

describe('정상 경로', () => {
  it('스텝 결과를 순서대로 합치고 degraded 는 false 다', async () => {
    // HOW: 각 스텝이 서로 다른 칸을 채운다. 마지막 state 에 셋 다 있어야 병합이 맞다.
    const ctx = makeCtx(1_000);
    const outcome = await runPipeline<State>(
      [okStep('a', { first: '1' }), okStep('b', { second: '2' }), okStep('c', { third: '3' })],
      {},
      ctx,
    );

    expect(outcome.state).toEqual({ first: '1', second: '2', third: '3' });
    expect(outcome.degraded).toBe(false);
    expect(outcome.degradedReasons).toEqual([]);
    expect(ctx.trace.toJSON().map((e) => e.status)).toEqual(['ok', 'ok', 'ok']);
  });

  it('뒤 스텝은 앞 스텝의 결과를 볼 수 있다', async () => {
    // WHY: facts → summary 처럼 앞의 산출물을 입력으로 쓰는 스텝이 실제로 있다.
    const ctx = makeCtx(1_000);
    const reader: Step<State> = {
      name: 'reader',
      async run(state) {
        return ok({ second: `앞에서 받은 값=${state.first ?? '없음'}` });
      },
    };

    const outcome = await runPipeline<State>([okStep('a', { first: '핵심' }), reader], {}, ctx);
    expect(outcome.state.second).toBe('앞에서 받은 값=핵심');
  });
});

describe('degrade — 부분 성공은 부분 성공이라고 말한다 (Principles §2.2)', () => {
  it('비필수 스텝이 degrade 하면 뒤 스텝은 계속 돈다', async () => {
    // WHY: 법령 검토가 실패해도 요약은 살아야 한다. 사용자는 남은 것만으로도
    //      판단을 이어갈 수 있고, 그것이 200 폴백 계약의 논리다.
    const ctx = makeCtx(1_000);
    const failing: Step<State> = {
      name: 'legal',
      async run() {
        return degrade('LEGAL', '법령 검토에 실패했습니다.');
      },
    };

    const outcome = await runPipeline<State>([failing, okStep('summary', { second: '요약' })], {}, ctx);

    expect(outcome.state.second).toBe('요약');
    expect(outcome.degraded).toBe(true);
    expect(outcome.degradedReasons).toEqual([{ code: 'LEGAL', message: '법령 검토에 실패했습니다.' }]);
    expect(ctx.trace.toJSON().map((e) => e.status)).toEqual(['degraded', 'ok']);
  });

  it('degrade 사유는 기계 판독용 코드와 사용자용 한국어를 함께 싣는다', async () => {
    // WHY: code 는 백엔드가 재시도를 판단하는 근거, message 는 화면에 그대로
    //      렌더링되는 계약이다(Principles §7.2). 둘을 하나로 합치면 둘 다 못 쓴다.
    const ctx = makeCtx(1_000);
    const outcome = await runPipeline<State>(
      [
        {
          name: 's',
          async run() {
            return degrade('SUMMARY', '요약 생성에 실패했습니다.');
          },
        },
      ],
      {},
      ctx,
    );

    const [reason] = outcome.degradedReasons;
    expect(reason?.code).toBe('SUMMARY');
    expect(reason?.message).toContain('실패했습니다');
  });
});

describe('fatal — 여기서 끝', () => {
  it('필수 스텝이 fatal 이면 요청 전체가 실패하고 뒤 스텝은 돌지 않는다', async () => {
    // WHY: clamp 가 실패하면 이후 스텝에 넣을 텍스트 자체가 없다. 계속 가면
    //      LLM 이 근거 없이 지어낸다 — 부분 결과보다 나쁜 결과다.
    const ctx = makeCtx(1_000);
    const clamp: Step<State> = {
      name: 'clamp',
      required: true,
      async run() {
        return fatal(new AiFailure('INPUT_TOO_LARGE', 'budget=0'));
      },
    };

    await expect(
      runPipeline<State>([clamp, okStep('after', { first: '와서는 안 됨' })], {}, ctx),
    ).rejects.toMatchObject({ code: 'INPUT_TOO_LARGE' });

    expect(ctx.trace.toJSON()).toEqual([
      expect.objectContaining({ step: 'clamp', status: 'fatal', note: 'INPUT_TOO_LARGE' }),
    ]);
  });
});

describe('스텝이 예외를 흘린 경우 = 우리 버그', () => {
  it('비필수 스텝의 예외는 degrade 로 흡수하되 반드시 로그를 남긴다', async () => {
    // WHY: 커널이 여기서 같이 죽으면 이미 만든 부분 결과를 전부 버리게 된다.
    //      대신 조용히 넘기지도 않는다 — 로그가 없으면 영원히 못 고친다.
    const ctx = makeCtx(1_000);
    const buggy: Step<State> = {
      name: 'buggy',
      async run() {
        throw new TypeError('undefined 의 속성을 읽으려 했다');
      },
    };

    const outcome = await runPipeline<State>([okStep('a', { first: '1' }), buggy], {}, ctx);

    expect(outcome.degraded).toBe(true);
    expect(outcome.degradedReasons[0]?.code).toBe('INTERNAL');
    expect(ctx.entries.some((e) => e.level === 'warn' && e.message === 'step threw')).toBe(true);
  });

  it('필수 스텝의 예외는 그대로 요청 실패다', async () => {
    const ctx = makeCtx(1_000);
    const buggy: Step<State> = {
      name: 'buggy',
      required: true,
      async run() {
        throw new TypeError('버그');
      },
    };

    await expect(runPipeline<State>([buggy], {}, ctx)).rejects.toMatchObject({ code: 'INTERNAL' });
  });

  it('던져진 것이 AiFailure 면 분류를 보존한다', async () => {
    // WHY: INTERNAL(500) 로 뭉개면 백엔드가 재시도 여부를 잘못 판단한다.
    //      LLM_UNAVAILABLE 은 재시도 가치가 있고 BAD_REQUEST 는 없다.
    const ctx = makeCtx(1_000);
    const step: Step<State> = {
      name: 'llm',
      async run() {
        throw new AiFailure('LLM_UNAVAILABLE', '엔드포인트 전멸');
      },
    };

    const outcome = await runPipeline<State>([step], {}, ctx);
    expect(outcome.degradedReasons[0]?.code).toBe('LLM_UNAVAILABLE');
  });
});

describe('데드라인 (Principles §4.1, §4.2)', () => {
  it('아무것도 만들지 못한 채 시간이 다하면 정직하게 실패한다', async () => {
    // WHY: 빈 결과를 200 으로 돌려주면 백엔드는 그걸 성공으로 캐시하고,
    //      그 공허한 결과가 영원히 재사용된다(ai-boundary.md §6.3).
    const ctx = makeCtx(0); // 이미 만료된 데드라인

    await expect(runPipeline<State>([okStep('a', { first: '1' })], {}, ctx)).rejects.toMatchObject({
      code: 'LLM_TIMEOUT',
    });
  });

  it('부분 결과가 있으면 남은 스텝을 건너뛰고 degraded 로 돌려준다', async () => {
    // HOW: 첫 스텝이 데드라인보다 오래 걸리게 만든다. 두 번째 스텝 진입 시점에
    //      이미 시간이 지나 있으므로 skipped 로 기록되어야 한다(전제 D).
    const ctx = makeCtx(20);
    const outcome = await runPipeline<State>(
      [okStep('slow', { first: '건진 값' }, 50), okStep('never', { second: '와서는 안 됨' })],
      {},
      ctx,
    );

    expect(outcome.state).toEqual({ first: '건진 값' });
    expect(outcome.degraded).toBe(true);
    expect(outcome.degradedReasons[0]?.code).toBe('DEADLINE');
    expect(ctx.trace.toJSON().map((e) => e.status)).toEqual(['ok', 'skipped']);
  });

  it('Deadline 은 남은 시간을 0 밑으로 내려보내지 않는다', () => {
    // WHY: 음수 예산이 setTimeout 으로 흘러가면 즉시 abort 가 되어
    //      "왜 모델을 부르지도 않고 타임아웃이지?" 라는 증상이 된다.
    const expired = Deadline.in(0);
    expect(expired.remainingMs()).toBe(0);
    expect(expired.expired()).toBe(true);
    expect(expired.budgetFor()).toBe(0);
  });

  it('budgetFor 는 자체 여유를 남긴다 — 백엔드 timeout 보다 먼저 끝나기 위해', () => {
    // WHY: CLAUDE.md §2-7 의 부등식. 우리가 리스를 넘기면 다른 워커가 같은 작업을
    //      다시 집고 LLM 비용이 두 배가 된다.
    const deadline = Deadline.in(5_000);
    expect(deadline.budgetFor(1_000)).toBeLessThanOrEqual(4_000);
    expect(deadline.budgetFor(1_000)).toBeGreaterThan(3_900);
  });
});

describe('Trace — 부산물이 아니라 산출물이다 (Principles §5.2)', () => {
  it('스텝마다 상태와 소요 시간을 남긴다', async () => {
    const ctx = makeCtx(1_000);
    await runPipeline<State>(
      [
        okStep('a', { first: '1' }, 5),
        {
          name: 'b',
          async run() {
            return degrade('B', '실패');
          },
        },
      ],
      {},
      ctx,
    );

    const entries = ctx.trace.toJSON();
    expect(entries).toHaveLength(2);
    expect(entries[0]).toMatchObject({ step: 'a', status: 'ok' });
    expect(entries[0]?.ms).toBeGreaterThanOrEqual(0);
    expect(entries[1]).toMatchObject({ step: 'b', status: 'degraded', note: 'B' });
  });

  it('toJSON 은 복사본을 준다 — 응답에 실린 뒤 바뀌면 안 된다', async () => {
    const ctx = makeCtx(1_000);
    await runPipeline<State>([okStep('a', { first: '1' })], {}, ctx);

    const snapshot = ctx.trace.toJSON();
    snapshot.push({ step: '침입', status: 'ok', ms: 0 });
    expect(ctx.trace.toJSON()).toHaveLength(1);
  });
});
