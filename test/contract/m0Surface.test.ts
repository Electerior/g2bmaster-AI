/**
 * ============================================================================
 * WHAT  — M0 이 실제로 여는 HTTP 표면 전부. ai-boundary.md §5 의 11개 엔드포인트.
 *         GET 3개는 진짜 답하고, POST 8개는 정직한 503 을 낸다.
 *
 * WHY   — CLAUDE.md §5: "M0 을 먼저 내는 이유는 폴백 계약이 정상 경로보다 먼저
 *         검증되어야 하기 때문이다." 백엔드는 AiUnavailableException 을 잡아
 *         프론트에 200 폴백을 만든다. 그 경로가 실제로 도는지는 AI 쪽이 분류된
 *         실패를 정확한 모양으로 내려보낼 때만 확인할 수 있다.
 *         또 응답 스키마는 스냅샷으로 고정한다(Principles §6.3) — 필드 하나가
 *         백엔드 파싱을 깨뜨린다.
 *
 * WHERE — src/app.ts + src/http/routes/*
 *
 * HOW   — 포트를 열지 않고 `app.inject()` 로 부른다. 라우팅·본문 파싱·스키마
 *         직렬화가 전부 실제로 돌기 때문에 "코드에는 있는데 응답에는 없는 필드" 를
 *         잡아낼 수 있다. 반대로 네트워크·프로세스는 개입하지 않아 빠르고 결정적이다.
 * ============================================================================
 */

import type { FastifyInstance } from 'fastify';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { buildApp } from '../../src/app.js';
import { PromptRegistry } from '../../src/capability/llm/promptRegistry.js';
import { FAILURES } from '../../src/http/errors.js';
import { PENDING_ENDPOINTS } from '../../src/http/routes/notImplemented.js';
import { TEST_CONFIG, promptFixture } from '../helpers/fixtures.js';

let app: FastifyInstance;

beforeAll(async () => {
  app = await buildApp({ config: TEST_CONFIG });
  await app.ready();
});

afterAll(async () => {
  await app.close();
});

describe('표면의 크기 — 우리가 소유하는 것은 11개다', () => {
  it('아직 열리지 않은 POST 는 8개다 (구현된 GET 3개와 합쳐 11개)', () => {
    // WHY: 이 숫자가 늘면 우리가 소유하지 않는 것을 구현하기 시작한 것이고(§4),
    //      줄면 백엔드의 AiClient 가 404 를 맞는다.
    expect(PENDING_ENDPOINTS).toHaveLength(8);
  });

  it('마일스톤 순서대로 열릴 표면이 명시돼 있다', () => {
    // WHY: "이 엔드포인트는 언제 되나요?" 에 코드가 답할 수 있어야 한다.
    expect(PENDING_ENDPOINTS.map((e) => `${e.milestone} ${e.path}`)).toEqual([
      'M1 /api/embed',
      'M2 /api/bid-summary',
      'M3 /api/item-summary',
      'M4 /api/price/resolve',
      'M4 /api/price/url',
      'M5 /api/legal/review-clauses',
      'M5 /api/legal/outreach-draft',
      'M5 /api/pledge/revision-workflow',
    ]);
  });
});

describe('아직 구현되지 않은 8개 — 정직한 503 (M0 의 핵심)', () => {
  it.each(PENDING_ENDPOINTS.map((e) => [e.path]))('%s 는 503 NOT_IMPLEMENTED 를 준다', async (path) => {
    const res = await app.inject({ method: 'POST', url: path, payload: { requestId: 'req-abc' } });

    expect(res.statusCode).toBe(503);
    expect(res.json()).toEqual({
      code: 'NOT_IMPLEMENTED',
      error: FAILURES.NOT_IMPLEMENTED.message,
      retryable: false,
      requestId: 'req-abc',
    });
  });

  it('본문이 비어 있어도 400 이 아니라 503 이다', async () => {
    // WHY: 아직 계약이 확정되지 않은 표면에 본문 검증을 걸면 백엔드가
    //      "형식이 틀렸다" 와 "아직 없다" 를 구분하지 못한다.
    const res = await app.inject({ method: 'POST', url: '/api/embed' });
    expect(res.statusCode).toBe(503);
    expect(res.json().code).toBe('NOT_IMPLEMENTED');
  });

  it('응답 본문에는 계약된 네 필드만 있다 (스키마가 나머지를 걷어낸다)', async () => {
    // WHY: 내부 진단(detail, 마일스톤 이름, 스택)이 새어 나가면 그대로 화면에 갈 수 있다.
    const res = await app.inject({ method: 'POST', url: '/api/embed', payload: {} });

    expect(Object.keys(res.json()).sort()).toEqual(['code', 'error', 'requestId', 'retryable']);
    expect(res.payload).not.toContain('pending');
  });

  it('requestId 는 본문 > 헤더 > 새로 생성 순으로 정해진다', async () => {
    // WHY: 백엔드 로그의 작업 하나와 우리 로그의 요청 하나를 잇는 유일한 실이다.
    const fromBody = await app.inject({
      method: 'POST',
      url: '/api/embed',
      headers: { 'x-request-id': '헤더값' },
      payload: { requestId: '본문값' },
    });
    expect(fromBody.json().requestId).toBe('본문값');

    const fromHeader = await app.inject({
      method: 'POST',
      url: '/api/embed',
      headers: { 'x-request-id': '헤더값' },
      payload: {},
    });
    expect(fromHeader.json().requestId).toBe('헤더값');

    const generated = await app.inject({ method: 'POST', url: '/api/embed', payload: {} });
    expect(generated.json().requestId).toMatch(/^[0-9a-f-]{36}$/);
  });
});

describe('GET /api/ai/prompt-version', () => {
  it('등록된 프롬프트가 없으면 null 과 빈 맵이다 (M0 의 실제 상태)', async () => {
    // WHY: "버전 없음" 을 빈 문자열로 위장하면 백엔드가 그걸 재사용 키에 넣고
    //      서로 다른 상태를 같은 키로 묶는다 — 캐시 오염이다.
    const res = await app.inject({ method: 'GET', url: '/api/ai/prompt-version' });

    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({ promptVersion: null, versions: {} });
  });

  it('프롬프트가 등록되면 단일 값과 엔드포인트별 맵을 함께 준다', async () => {
    // WHY: ai-boundary.md §6.1 은 단일 문자열, CLAUDE.md 전제 F 는 맵을 말한다.
    //      전제가 미확인이므로 둘 다 싣는다 — 필드 추가는 자유다(Principles §7.1).
    const prompts = new PromptRegistry();
    prompts.register('item-summary', promptFixture());
    prompts.register('bid-summary', promptFixture({ label: 'bid-summary', system: '너는 영업 요약기다.' }));

    const withPrompts = await buildApp({ config: TEST_CONFIG, prompts });
    await withPrompts.ready();
    const body = (await withPrompts.inject({ method: 'GET', url: '/api/ai/prompt-version' })).json();
    await withPrompts.close();

    expect(body.promptVersion).toBe(body.versions['item-summary']);
    expect(body.versions['bid-summary']).toMatch(/^bid-summary-[0-9a-f]{12}$/);
  });
});

describe('GET /api/ai/capacity', () => {
  it('슬롯 수를 보고하되 모델에 닿지 못한다는 사실을 숨기지 않는다', async () => {
    // WHY: 백엔드는 이 값으로 내보내기 ETA 를 계산한다. maxConcurrency 만 보고
    //      네 건을 밀어넣으면 전부 503 을 받는다 — 우리가 거짓말을 한 것이다(§2.3).
    const res = await app.inject({ method: 'GET', url: '/api/ai/capacity' });

    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({
      maxConcurrency: TEST_CONFIG.llm.maxConcurrency,
      inFlight: 0,
      availableSlots: 0,
      llmReachable: false,
    });
    expect(Date.parse(res.json().checkedAt)).not.toBeNaN();
  });
});

describe('GET /api/llm/models', () => {
  it('모델 백엔드가 없으면 빈 목록과 reachable=false 다', async () => {
    const res = await app.inject({ method: 'GET', url: '/api/llm/models' });

    expect(res.statusCode).toBe(200);
    expect(res.json()).toMatchObject({ models: [], reachable: false });
  });
});

describe('경계 바깥', () => {
  it('우리가 소유하지 않는 경로는 404 이고, 본문 모양은 다른 실패와 같다', async () => {
    // WHY: 백엔드는 모든 실패를 같은 파서로 읽는다. 404 만 모양이 다르면 거기서 깨진다.
    //      이 경로는 실제로 §4 가 "우리에게 없다" 고 못박은 것이다.
    const res = await app.inject({ method: 'POST', url: '/api/analysis-jobs/status', payload: {} });

    expect(res.statusCode).toBe(404);
    expect(Object.keys(res.json()).sort()).toEqual(['code', 'error', 'requestId', 'retryable']);
    expect(res.json().retryable).toBe(false);
  });

  it('POST 표면에 GET 으로 오면 404 다 (메서드도 계약의 일부다)', async () => {
    const res = await app.inject({ method: 'GET', url: '/api/embed' });
    expect(res.statusCode).toBe(404);
  });
});

describe('전역 오류 처리 — 어떤 실패도 같은 모양으로 나간다', () => {
  it('깨진 JSON 본문은 BAD_REQUEST(400) 이지 INTERNAL(500) 이 아니다', async () => {
    // WHY: 이걸 500/retryable 로 분류하면, 절대 성공할 수 없는 요청을 백엔드가
    //      재시도 예산이 다할 때까지 반복한다. 호출자 쪽 오타 하나가 큐를 굶긴다.
    // HOW: Fastify 의 본문 파서가 던지는 오류에는 validation 이 없고 statusCode 만 400 이다.
    //      그래서 판별을 validation 하나에만 걸면 이 경로가 통째로 새어 나간다.
    const res = await app.inject({
      method: 'POST',
      url: '/api/embed',
      headers: { 'content-type': 'application/json' },
      payload: '{"requestId": ',
    });

    expect(res.statusCode).toBe(400);
    expect(res.json()).toMatchObject({ code: 'BAD_REQUEST', retryable: false });
  });

  it('오류 응답에도 requestId 가 반드시 실린다', async () => {
    // WHY: 실패한 요청일수록 두 저장소의 로그를 이어 봐야 한다.
    const res = await app.inject({
      method: 'POST',
      url: '/api/embed',
      headers: { 'content-type': 'application/json', 'x-request-id': '추적용-ID' },
      payload: '{깨짐',
    });

    expect(res.json().requestId).toBe('추적용-ID');
  });
});
