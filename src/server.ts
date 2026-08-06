/**
 * 프로세스 진입점. 하는 일은 세 가지뿐이다 — 설정 검증, listen, 정상 종료.
 *
 * 내구 상태가 없으므로(CLAUDE.md §2-10) 종료 시 비울 것도 없다.
 * `app.close()` 로 진행 중이던 요청만 마무리하면 끝이다.
 */

import { buildApp } from './app.js';
import { ConfigError, loadConfig } from './config.js';

async function main(): Promise<void> {
  const config = loadConfig();
  const app = await buildApp({ config });

  const shutdown = (signal: string): void => {
    app.log.info({ signal }, 'shutting down');
    // 진행 중이던 요청 하나를 잃는 것이 최대 손실이다(Principles §1.2).
    void app.close().then(
      () => process.exit(0),
      (err: unknown) => {
        app.log.error({ err }, 'shutdown failed');
        process.exit(1);
      },
    );
  };

  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));

  await app.listen({ port: config.port, host: config.host });
}

main().catch((err: unknown) => {
  // 로거가 서기 전에 죽을 수 있으므로 stderr 로 직접 쓴다.
  const reason = err instanceof ConfigError ? `설정 오류: ${err.message}` : String(err);
  process.stderr.write(`g2bmaster-ai 기동 실패 — ${reason}\n`);
  process.exit(1);
});
