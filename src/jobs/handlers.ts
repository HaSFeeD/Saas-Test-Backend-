import type { Ctx } from '../context.js';
import type { Mail } from '../lib/mailer.js';
import { runSearchJob } from '../modules/searches/service.js';

export function registerJobs(ctx: Ctx) {
  ctx.worker
    .register('run_search', (p: { searchId: string }, job) => runSearchJob(ctx, p.searchId, job.isLastAttempt))
    .register('send_email', (p: Mail) => ctx.mailer.send(p));
}
