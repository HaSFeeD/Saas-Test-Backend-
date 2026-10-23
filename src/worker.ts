// Standalone worker process: `npm run worker`. Scale it independently of the API.
import { buildApp } from './app.js';
import { loadConfig } from './config.js';

const { app, ctx } = await buildApp({ cfg: loadConfig() });
ctx.worker.start();
app.log.info('worker started');

for (const sig of ['SIGINT', 'SIGTERM'] as const) {
  process.once(sig, async () => {
    await app.close();
    process.exit(0);
  });
}
