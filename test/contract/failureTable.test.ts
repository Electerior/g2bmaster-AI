/**
 * ============================================================================
 * WHAT  — 실패 분류표(FAILURES)와 HTTP 매핑(toHttp)의 스냅샷.
 *
 * WHY   — 이 표는 두 저장소가 공유하는 계약이다. 백엔드 워커는 HTTP status 가 아니라
 *         `code` 와 `retryable` 로 재시도 여부를 판단한다. 그래서 값이 하나만 바뀌어도
 *         백엔드의 동작이 조용히 바뀐다 — 예를 들어 retryable 이 true 로 뒤집히면
 *         영구 실패를 무한히 재시도하며 큐의 다른 작업을 굶긴다.
 *         CLAUDE.md §2-1: 실패를 200 으로 포장하지 않는다. 여기가 그 집행 지점이다.
 *
 * WHERE — src/http/errors.ts
 *
 * HOW   — 표 전체를 손으로 적어 대조한다. 자동 생성된 스냅샷 파일을 쓰지 않는 이유는,
 *         스냅샷은 `-u` 한 번으로 무심코 갱신되지만 여기 적힌 값은 고치려면
 *         **왜 고치는지 생각하게 되기** 때문이다. 계약은 그렇게 다뤄야 한다.
 * ============================================================================
 */

import { describe, expect, it } from 'vitest';

import { AiFailure, FAILURES, toHttp, type FailureCode } from '../../src/http/errors.js';

/**
 * 계약 그 자체. 백엔드와 합의된 값이므로 여기를 고치려면 백엔드도 같이 고쳐야 한다.
 * 관례: 4xx = 영구 실패(재시도 금지), 5xx = 일시 실패(재시도 가능).
 * 단 NOT_IMPLEMENTED 는 의도적으로 그 관례를 벗어난다 — 아래 별도 케이스 참조.
 */
const EXPECTED: Record<FailureCode, { status: number; retryable: boolean }> = {
  BAD_REQUEST: { status: 400, retryable: false },
  INPUT_TOO_LARGE: { status: 400, retryable: false },
  TAG_MISSING: { status: 400, retryable: false },
  NOT_IMPLEMENTED: { status: 503, retryable: false },
  LLM_UNAVAILABLE: { status: 503, retryable: true },
  LLM_TIMEOUT: { status: 504, retryable: true },
  LLM_MALFORMED: { status: 502, retryable: true },
  INTERNAL: { status: 500, retryable: true },
};

describe('실패 분류표', () => {
  it('표의 모든 항목이 합의된 status·retryable 을 갖는다', () => {
    for (const [code, expected] of Object.entries(EXPECTED)) {
      expect(FAILURES[code as FailureCode]).toMatchObject(expected);
    }
  });

  it('표에 없는 코드도, 코드가 없는 표 항목도 없다', () => {
    // WHY: 코드를 추가하고 표를 안 고치면 런타임에 undefined 를 읽고 500 이 된다.
    expect(Object.keys(FAILURES).sort()).toEqual(Object.keys(EXPECTED).sort());
  });

  it('모든 문구는 사용자에게 그대로 보여줄 한국어다', () => {
    // WHY: 이 문자열은 aiError 로 화면에 렌더링된다(Principles §7.2).
    //      영문 예외 메시지나 스택이 새어 나가면 그대로 사용자가 본다.
    for (const spec of Object.values(FAILURES)) {
      expect(spec.message.length).toBeGreaterThan(0);
      expect(spec.message).toMatch(/[가-힣]/u);
      expect(spec.message).not.toMatch(/Error|undefined|null/);
    }
  });

  it('표는 얼려 둔다 — 런타임에 분류가 바뀌면 재현이 불가능해진다', () => {
    expect(Object.isFrozen(FAILURES)).toBe(true);
  });

  it('NOT_IMPLEMENTED 는 503 이면서 재시도 불가다 (의도된 예외)', () => {
    // WHY: 503 인 이유 — 백엔드가 AiUnavailableException 경로로 받아 200 폴백을
    //      만들게 하려는 것이다. M0 의 존재 이유가 그 경로의 선검증이다.
    //      retryable=false 인 이유 — 지금 다시 불러도 결과가 같다. 마일스톤이
    //      배포되기 전까지 재시도는 순수한 낭비이고 큐의 다른 작업을 굶긴다.
    //      status 와 retryable 이 갈리는 것은 "백엔드는 status 가 아니라 code 로
    //      판단한다" 는 관례가 있기에 표현 가능하다. docs/decisions.md D-002.
    expect(FAILURES.NOT_IMPLEMENTED).toMatchObject({ status: 503, retryable: false });
  });
});

describe('AiFailure', () => {
  it('사용자 문구는 표에서 오고, 내부 진단은 detail 에 따로 담는다', () => {
    // WHY: 내부 원인이 message 에 섞이면 화면에 그대로 나간다. 분리는 계약이다.
    const failure = new AiFailure('LLM_TIMEOUT', 'aborted after 30000ms');

    expect(failure.message).toBe(FAILURES.LLM_TIMEOUT.message);
    expect(failure.detail).toBe('aborted after 30000ms');
    expect(failure.retryable).toBe(true);
  });
});

describe('toHttp', () => {
  it('AiFailure 를 status + 계약 본문으로 바꾼다', () => {
    const { status, body } = toHttp(new AiFailure('INPUT_TOO_LARGE', 'budget=0'), 'req-1');

    expect(status).toBe(400);
    expect(body).toEqual({
      code: 'INPUT_TOO_LARGE',
      error: FAILURES.INPUT_TOO_LARGE.message,
      retryable: false,
      requestId: 'req-1',
    });
  });

  it('내부 진단(detail)은 응답 본문에 절대 넣지 않는다', () => {
    // WHY: detail 에는 파일 경로·모델 이름·엔드포인트 주소가 들어간다.
    //      이건 로그의 몫이지 사용자나 프론트로 나갈 값이 아니다.
    const { body } = toHttp(new AiFailure('INTERNAL', '/srv/g2b/secret/path.js:42'), 'req-2');

    expect(JSON.stringify(body)).not.toContain('secret');
  });

  it('retryAfterMs 는 있을 때만 싣는다', () => {
    // WHY: 값이 없을 때 null 이나 0 을 실으면 백엔드가 "0ms 뒤 재시도" 로 읽는다.
    expect(toHttp(new AiFailure('LLM_UNAVAILABLE'), 'r').body).not.toHaveProperty('retryAfterMs');
    expect(toHttp(new AiFailure('LLM_UNAVAILABLE', 'pool', 5_000), 'r').body.retryAfterMs).toBe(5_000);
  });

  it('분류되지 않은 예외는 INTERNAL 로 떨어진다 — 절대 200 이 되지 않는다', () => {
    // WHY: 여기서 어떤 값이 새어 나가도 200 은 나오면 안 된다.
    //      우리가 200 으로 포장하는 순간 백엔드는 실패를 구분할 근거를 잃는다.
    for (const thrown of [new TypeError('버그'), '문자열이 던져짐', undefined, { 이상한: '객체' }]) {
      const { status, body } = toHttp(thrown, 'req-3');
      expect(status).toBe(500);
      expect(body.code).toBe('INTERNAL');
      expect(body.error).toBe(FAILURES.INTERNAL.message);
    }
  });
});
