/**
 * ============================================================================
 * WHAT  — 설정 로딩과 검증(loadConfig).
 *
 * WHY   — 틀린 설정으로 그냥 뜨면 첫 요청에서야 터진다. 그 시점에는 백엔드 워커가
 *         이미 작업을 물고 리스를 태우고 있고, 사용자는 기다리는 중이다.
 *         기동을 거부하면 배포 파이프라인이 즉시 알려 준다 — 훨씬 싸다.
 *         특히 LLM_MAX_CONCURRENCY 는 우리 스스로 거는 상한이라(Principles §4.3)
 *         0 이나 음수가 들어가면 서비스가 조용히 아무 일도 안 하게 된다.
 *
 * WHERE — src/config.ts
 *
 * HOW   — process.env 를 건드리지 않는다. loadConfig 가 env 객체를 인자로 받도록
 *         만들어 뒀기 때문에, 테스트끼리 전역 상태를 오염시키지 않고 순수하게 검증한다.
 * ============================================================================
 */

import { describe, expect, it } from 'vitest';

import { ConfigError, loadConfig } from '../../src/config.js';

describe('기본값', () => {
  it('환경변수가 하나도 없어도 뜬다', () => {
    // WHY: 개발자가 .env 없이 클론하고 바로 돌릴 수 있어야 한다.
    //      비밀값이 필요한 기능은 M1 이후에 붙으므로 그때는 필수 검증이 늘어난다.
    expect(loadConfig({})).toEqual({
      port: 8_100,
      host: '0.0.0.0',
      logLevel: 'info',
      llm: { maxConcurrency: 4, perCallCapMs: 60_000 },
      contextWindowTokens: 32_768,
    });
  });

  it('빈 문자열은 "설정하지 않음" 과 같게 취급한다', () => {
    // WHY: 컨테이너 환경에서 PORT= 처럼 값 없이 선언되는 일이 흔하다.
    //      이걸 0 으로 읽으면 임의 포트에 뜨고, 아무도 접속하지 못한다.
    expect(loadConfig({ PORT: '', LOG_LEVEL: '' })).toMatchObject({ port: 8_100, logLevel: 'info' });
  });
});

describe('값 검증 — 틀리면 기동을 거부한다', () => {
  it.each([
    ['PORT', '0'],
    ['PORT', '70000'],
    ['PORT', '8100.5'],
    ['PORT', '팔천백'],
    ['LLM_MAX_CONCURRENCY', '0'], // 0 이면 아무 요청도 처리하지 못한 채 매달린다
    ['LLM_MAX_CONCURRENCY', '-1'],
    ['LLM_PER_CALL_CAP_MS', '10'], // 1초 미만은 모델이 첫 토큰도 못 낸다
    ['CONTEXT_WINDOW_TOKENS', '100'],
  ])('%s=%s 는 ConfigError 다', (key, value) => {
    expect(() => loadConfig({ [key]: value })).toThrow(ConfigError);
  });

  it('오류 문구에 키 이름과 받은 값이 들어간다', () => {
    // WHY: 기동 실패 로그 한 줄로 무엇을 고쳐야 하는지 알 수 있어야 한다.
    expect(() => loadConfig({ PORT: '70000' })).toThrow(/PORT[\s\S]*70000/);
  });

  it('알 수 없는 로그 레벨은 거부한다', () => {
    // WHY: 오타(inf, verbose)를 그냥 통과시키면 로거가 조용해지고,
    //      장애가 났을 때 아무 기록도 남지 않는다.
    expect(() => loadConfig({ LOG_LEVEL: 'verbose' })).toThrow(ConfigError);
    expect(loadConfig({ LOG_LEVEL: 'debug' }).logLevel).toBe('debug');
    expect(loadConfig({ LOG_LEVEL: 'silent' }).logLevel).toBe('silent');
  });
});

describe('정상 값', () => {
  it('환경변수를 그대로 반영한다', () => {
    const config = loadConfig({
      PORT: '9000',
      HOST: '127.0.0.1',
      LOG_LEVEL: 'warn',
      LLM_MAX_CONCURRENCY: '8',
      LLM_PER_CALL_CAP_MS: '45000',
      CONTEXT_WINDOW_TOKENS: '131072',
    });

    expect(config).toEqual({
      port: 9_000,
      host: '127.0.0.1',
      logLevel: 'warn',
      llm: { maxConcurrency: 8, perCallCapMs: 45_000 },
      contextWindowTokens: 131_072,
    });
  });
});
