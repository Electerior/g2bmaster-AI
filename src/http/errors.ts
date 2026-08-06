/**
 * 실패 분류의 단일 출처.
 *
 * 규칙(CLAUDE.md §2-1): AI→백엔드 구간에서 실패는 정직한 4xx/5xx로 내린다.
 * HTTP 200 폴백은 백엔드 컨트롤러가 AiUnavailableException 을 잡아 만드는 것이다.
 *
 * 관례: 4xx = 영구 실패(재시도 금지), 5xx = 일시 실패(재시도 가능).
 * 백엔드 워커는 status 가 아니라 code / retryable 로 판단한다.
 */

export type FailureCode =
  | 'BAD_REQUEST'
  | 'INPUT_TOO_LARGE'
  | 'TAG_MISSING'
  | 'NOT_IMPLEMENTED'
  | 'LLM_UNAVAILABLE'
  | 'LLM_TIMEOUT'
  | 'LLM_MALFORMED'
  | 'INTERNAL';

interface FailureSpec {
  status: number;
  retryable: boolean;
  /** 사용자에게 그대로 렌더링되는 한국어 문구. 코드에서 조립하지 않는다. */
  message: string;
}

export const FAILURES: Readonly<Record<FailureCode, FailureSpec>> = Object.freeze({
  BAD_REQUEST: {
    status: 400,
    retryable: false,
    message: '요청 형식이 올바르지 않습니다.',
  },
  INPUT_TOO_LARGE: {
    status: 400,
    retryable: false,
    message: '문서 분량이 너무 많아 분석할 수 없습니다.',
  },
  TAG_MISSING: {
    status: 400,
    retryable: false,
    message: '문서 태그가 확인되지 않아 서약서 검토를 시작할 수 없습니다.',
  },
  // 아직 구현되지 않은 마일스톤 표면(M0).
  // 503 인 이유: 백엔드가 AiUnavailableException 경로로 받아 200 폴백을 만들게 하려는 것이다.
  //   501 로 내리면 그 경로가 안 타고, M0 의 존재 이유인 "폴백 계약 선검증"이 무의미해진다.
  // retryable=false 인 이유: 지금 다시 불러도 결과가 달라지지 않는다. 워커가 재시도 예산을
  //   태우게 두면 큐의 다른 작업이 굶는다. status 와 retryable 이 갈리는 것은 의도된 것이며,
  //   백엔드는 status 가 아니라 code/retryable 로 판단한다(위 관례).
  // 백엔드와 합의가 필요한 항목이다 — docs/decisions.md D-002.
  NOT_IMPLEMENTED: {
    status: 503,
    retryable: false,
    message: 'AI 분석 기능이 아직 준비되지 않았습니다.',
  },
  LLM_UNAVAILABLE: {
    status: 503,
    retryable: true,
    message: 'AI 분석 서버에 연결할 수 없어 분석을 완료하지 못했습니다.',
  },
  LLM_TIMEOUT: {
    status: 504,
    retryable: true,
    message: 'AI 분석이 제한 시간 내에 끝나지 않았습니다.',
  },
  LLM_MALFORMED: {
    status: 502,
    retryable: true,
    message: 'AI 응답을 해석하지 못했습니다.',
  },
  INTERNAL: {
    status: 500,
    retryable: true,
    message: '분석 처리 중 오류가 발생했습니다.',
  },
});

export class AiFailure extends Error {
  readonly code: FailureCode;
  /** 내부 진단용. 응답 본문에 넣지 않는다(로그 전용). */
  readonly detail: string | undefined;
  readonly retryAfterMs: number | undefined;

  constructor(code: FailureCode, detail?: string, retryAfterMs?: number) {
    super(FAILURES[code].message);
    this.name = 'AiFailure';
    this.code = code;
    this.detail = detail;
    this.retryAfterMs = retryAfterMs;
  }

  get retryable(): boolean {
    return FAILURES[this.code].retryable;
  }
}

export interface ErrorBody {
  code: FailureCode;
  /** aiError 로 화면에 노출된다. */
  error: string;
  retryable: boolean;
  retryAfterMs?: number;
  requestId: string;
}

export function toHttp(err: unknown, requestId: string): { status: number; body: ErrorBody } {
  const failure =
    err instanceof AiFailure ? err : new AiFailure('INTERNAL', err instanceof Error ? err.message : String(err));
  const spec = FAILURES[failure.code];
  return {
    status: spec.status,
    body: {
      code: failure.code,
      error: spec.message,
      retryable: spec.retryable,
      ...(failure.retryAfterMs !== undefined ? { retryAfterMs: failure.retryAfterMs } : {}),
      requestId,
    },
  };
}
