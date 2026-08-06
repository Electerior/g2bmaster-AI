import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['test/**/*.test.ts'],
    // 기본 테스트는 모델 없이 돈다(Principles §6.1). test/eval/ 은 M3 에서 붙인다.
    environment: 'node',
    coverage: {
      provider: 'v8',
      include: ['src/**/*.ts'],
      exclude: [
        // listen 만 한다. 계약 검증은 app.ts 를 통해 inject 로 한다.
        'src/server.ts',
        // M3 대기 중. 코드는 트리에 있지만 라우트가 등록되지 않았고 호출할 ChatClient 도
        // 없다. 여기에 커버리지를 요구하면 아직 계약이 확정되지 않은 표면에 테스트를
        // 먼저 박게 되고, M3 에서 그 테스트를 다시 뜯게 된다.
        // → M3 에서 이 두 줄을 지우는 것이 마일스톤의 DoD 다. docs/decisions.md D-003.
        'src/pipeline/item-summary/**',
        'src/http/routes/itemSummary.ts',
      ],
      thresholds: {
        lines: 80,
        functions: 80,
        branches: 80,
        statements: 80,
      },
    },
  },
});
