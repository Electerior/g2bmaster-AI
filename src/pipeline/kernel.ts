/**
 * 파이프라인 커널.
 *
 * 설계 의도:
 *  - 스텝은 예외를 던지지 않고 ok / degrade / fatal 중 하나를 "선택"한다.
 *    예외를 쓰면 이 선택이 사라지고 전부 500 이 된다(CLAUDE.md §2-2).
 *  - 데드라인은 호출자가 준다. 스텝 진입마다 확인한다(§2-7).
 *  - 부분 결과가 있는 상태에서 시간이 다하면 degraded 로 반환한다(전제 D).
 */

import { AiFailure } from '../http/errors.js';

export interface Degrade {
  /** 기계 판독용. 백엔드가 재시도 여부를 판단하는 데 쓴다. */
  code: string;
  /** 사용자 노출용 한국어. */
  message: string;
}

export type StepResult<T> =
  | { ok: true; value: T }
  | { ok: false; kind: 'degrade'; degrade: Degrade }
  | { ok: false; kind: 'fatal'; fatal: AiFailure };

export const ok = <T>(value: T): StepResult<T> => ({ ok: true, value });
export const degrade = <T>(code: string, message: string): StepResult<T> => ({
  ok: false,
  kind: 'degrade',
  degrade: { code, message },
});
export const fatal = <T>(f: AiFailure): StepResult<T> => ({ ok: false, kind: 'fatal', fatal: f });

export class Deadline {
  private constructor(private readonly at: number) {}

  static in(ms: number): Deadline {
    return new Deadline(Date.now() + ms);
  }

  remainingMs(): number {
    return Math.max(0, this.at - Date.now());
  }

  expired(): boolean {
    return this.remainingMs() <= 0;
  }

  /** LLM 호출에 줄 시간. 자체 여유를 남겨 백엔드 timeout 보다 먼저 끝낸다. */
  budgetFor(reserveMs = 1_000): number {
    return Math.max(0, this.remainingMs() - reserveMs);
  }
}

export interface TraceEntry {
  step: string;
  status: 'ok' | 'degraded' | 'skipped' | 'fatal';
  ms: number;
  note?: string;
}

export class Trace {
  private readonly entries: TraceEntry[] = [];

  record(e: TraceEntry): void {
    this.entries.push(e);
  }

  /** sourceTrace 는 부산물이 아니라 산출물이다(Principles §5.2). */
  toJSON(): TraceEntry[] {
    return [...this.entries];
  }
}

export interface Ctx {
  requestId: string;
  deadline: Deadline;
  trace: Trace;
  promptVersion: string;
  log: { info(o: object, m?: string): void; warn(o: object, m?: string): void };
}

export interface Step<S> {
  name: string;
  /**
   * true 면 실패가 곧 요청 실패다.
   * false(기본)면 degrade 로 기록하고 다음 스텝으로 넘어간다.
   */
  required?: boolean;
  run(state: S, ctx: Ctx): Promise<StepResult<Partial<S>>>;
}

export interface PipelineOutcome<S> {
  state: S;
  degraded: boolean;
  degradedReasons: Degrade[];
}

export async function runPipeline<S extends object>(
  steps: ReadonlyArray<Step<S>>,
  initial: S,
  ctx: Ctx,
): Promise<PipelineOutcome<S>> {
  let state = initial;
  const reasons: Degrade[] = [];
  let produced = false;

  for (const step of steps) {
    if (ctx.deadline.expired()) {
      // 아무것도 못 만들었으면 정직하게 실패. 부분 결과가 있으면 degraded 로 내보낸다.
      if (!produced) throw new AiFailure('LLM_TIMEOUT', `deadline expired before ${step.name}`);
      ctx.trace.record({ step: step.name, status: 'skipped', ms: 0, note: 'deadline' });
      reasons.push({ code: 'DEADLINE', message: '시간 제한으로 일부 분석을 건너뛰었습니다.' });
      continue;
    }

    const startedAt = Date.now();
    let result: StepResult<Partial<S>>;
    try {
      result = await step.run(state, ctx);
    } catch (e) {
      // 스텝이 예외를 흘렸다 = 버그. 필수 스텝이면 요청 실패, 아니면 degrade.
      const f = e instanceof AiFailure ? e : new AiFailure('INTERNAL', (e as Error)?.message);
      result = step.required ? fatal(f) : degrade(f.code, f.message);
      ctx.log.warn({ requestId: ctx.requestId, step: step.name, err: String(e) }, 'step threw');
    }
    const ms = Date.now() - startedAt;

    if (result.ok) {
      state = { ...state, ...result.value };
      produced = true;
      ctx.trace.record({ step: step.name, status: 'ok', ms });
      continue;
    }

    if (result.kind === 'fatal') {
      ctx.trace.record({ step: step.name, status: 'fatal', ms, note: result.fatal.code });
      throw result.fatal;
    }

    reasons.push(result.degrade);
    ctx.trace.record({ step: step.name, status: 'degraded', ms, note: result.degrade.code });
  }

  return { state, degraded: reasons.length > 0, degradedReasons: reasons };
}
