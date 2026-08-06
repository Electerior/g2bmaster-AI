/**
 * ============================================================================
 * WHAT  — 프롬프트 버전 자동 파생 규칙.
 *
 * WHY   — 이 값은 백엔드의 **분석 결과 재사용 키** 에 들어간다(ai-boundary.md §6.1).
 *         두 방향 모두 사고다:
 *           · 의미가 바뀌었는데 버전이 그대로 → 백엔드가 낡은 결과를 영원히 재사용한다.
 *             "고쳤는데 화면이 안 바뀐다" 로 며칠을 잃는다.
 *           · 의미가 그대로인데 버전이 흔들림 → 캐시가 통째로 고아가 되고 전부 재추론한다.
 *         CLAUDE.md §2-4 가 "손으로 문자열을 고치는 방식은 반드시 잊어버린다" 고
 *         못박은 이유가 이것이다.
 *
 * WHERE — src/capability/llm/promptRegistry.ts
 *
 * HOW   — 해시 문자열 자체를 박아 두지 않는다. 그 값은 트랜스파일러가 함수 소스를
 *         어떻게 출력하느냐에 따라 달라지기 때문이다(docs/decisions.md D-004 참조).
 *         대신 **차분(differential) 검증** 을 한다: 의미를 바꾸는 축을 하나씩 흔들어
 *         버전이 반드시 바뀌는지, 안 흔들면 반드시 그대로인지를 본다.
 * ============================================================================
 */

import { describe, expect, it } from 'vitest';

import { PromptRegistry, type PromptDef } from '../../src/capability/llm/promptRegistry.js';
import { promptFixture } from '../helpers/fixtures.js';

/** 같은 정의를 새 레지스트리에 넣고 파생된 버전만 꺼내는 헬퍼. */
function versionOf(def: PromptDef = promptFixture()): string {
  return new PromptRegistry().register('p', def).version;
}

/** 라벨 접두사를 뗀 해시 부분. */
function hashOf(version: string): string {
  return version.slice(version.lastIndexOf('-') + 1);
}

describe('버전 형식', () => {
  it('사람이 읽는 라벨을 접두사로 두고 해시를 붙인다', () => {
    // 라벨은 사람용이지 식별자가 아니다 — 뒤의 해시가 실제 식별자다.
    expect(versionOf()).toMatch(/^item-summary-[0-9a-f]{12}$/);
  });
});

describe('같은 정의 → 같은 버전 (재사용 키가 흔들리지 않는다)', () => {
  it('두 번 파생해도 동일하다', () => {
    expect(versionOf()).toBe(versionOf());
  });

  it('의미와 무관한 필드(label)만 바꿔도 해시 부분은 유지된다', () => {
    // WHY: 라벨은 해시 재료가 아니다. 사람이 읽기 좋게 라벨을 고쳤다는 이유로
    //      백엔드 캐시를 통째로 버리게 만들면 안 된다.
    expect(hashOf(versionOf(promptFixture({ label: 'item-summary-v9' })))).toBe(hashOf(versionOf()));
  });
});

describe('출력 의미를 바꾸는 축 → 버전이 반드시 바뀐다 (Principles §3.1)', () => {
  const base = versionOf();

  it('시스템 프롬프트 문구', () => {
    expect(versionOf(promptFixture({ system: '너는 조달 공고 분석기다. 한국어로만 답한다.' }))).not.toBe(base);
  });

  it('모델 ID — 같은 프롬프트라도 모델이 다르면 다른 결과다', () => {
    expect(versionOf(promptFixture({ model: 'qwen2.5-14b-instruct' }))).not.toBe(base);
  });

  it('디코딩 파라미터 — temperature', () => {
    expect(versionOf(promptFixture({ decoding: { temperature: 0.7, topP: 0.9, maxTokens: 2_048 } }))).not.toBe(base);
  });

  it('디코딩 파라미터 — maxTokens (잘린 출력은 다른 출력이다)', () => {
    expect(versionOf(promptFixture({ decoding: { temperature: 0.2, topP: 0.9, maxTokens: 512 } }))).not.toBe(base);
  });

  it('사용자 메시지 템플릿', () => {
    // HOW: render 함수의 소스 텍스트가 해시 재료다. 템플릿을 고치면 버전이 따라 올라간다.
    expect(versionOf(promptFixture({ render: (input) => `분석 대상:\n${JSON.stringify(input)}` }))).not.toBe(base);
  });

  it('후처리·파싱 개정 번호 — 프롬프트가 같아도 파싱이 바뀌면 결과가 바뀐다', () => {
    // WHY: 이 필드가 존재하는 이유. 프롬프트 문자열은 그대로인데 파서만 고친 경우를
    //      자동으로 감지할 방법이 없어서, 사람이 올리는 유일한 손잡이로 남겨 뒀다.
    expect(versionOf(promptFixture({ postprocessRev: 2 }))).not.toBe(base);
  });
});

describe('레지스트리 자체의 계약', () => {
  it('같은 ID 를 두 번 등록하면 즉시 터진다', () => {
    // WHY: 조용히 덮어쓰면 어떤 프롬프트가 실제로 쓰이는지 알 수 없게 되고,
    //      prompt-version 응답이 거짓말이 된다. 기동 시점에 죽는 편이 낫다.
    const registry = new PromptRegistry();
    registry.register('item-summary.facts', promptFixture());

    expect(() => registry.register('item-summary.facts', promptFixture())).toThrow(/duplicate/);
  });

  it('없는 ID 를 꺼내면 터진다', () => {
    expect(() => new PromptRegistry().get('없는-프롬프트')).toThrow(/unknown prompt id/);
  });

  it('versionMap 은 등록된 전부를 엔드포인트별로 돌려준다', () => {
    // WHY: GET /api/ai/prompt-version 이 맵을 주는 이유(CLAUDE.md §6 전제 F).
    //      bid-summary 와 item-summary 는 프롬프트가 다르므로 버전도 달라야 한다.
    const registry = new PromptRegistry();
    registry.register('item-summary', promptFixture());
    registry.register('bid-summary', promptFixture({ label: 'bid-summary', system: '너는 영업 요약기다.' }));

    const map = registry.versionMap();
    expect(Object.keys(map).sort()).toEqual(['bid-summary', 'item-summary']);
    expect(map['bid-summary']).not.toBe(map['item-summary']);
  });

  it('빈 레지스트리의 versionMap 은 빈 객체다', () => {
    // M0 의 실제 상태. 등록된 프롬프트가 없으면 "버전 없음" 이라고 말해야 한다.
    expect(new PromptRegistry().versionMap()).toEqual({});
  });
});
