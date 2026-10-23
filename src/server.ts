import { buildApp } from './app.js';
import { loadConfig } from './config.js';
import { migrate } from './db.js';

const cfg = loadConfig();
const { app, ctx } = await buildApp({ cfg });

await migrate(ctx.db);
if (cfg.RUN_WORKER) ctx.worker.start();
await app.listen({ port: cfg.PORT, host: '0.0.0.0' });

for (const sig of ['SIGINT', 'SIGTERM'] as const) {
  process.once(sig, async () => {
    app.log.info({ sig }, 'shutting down');
    await app.close();
    process.exit(0);
  });
}
