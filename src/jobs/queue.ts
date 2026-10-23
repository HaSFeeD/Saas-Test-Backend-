import type { FastifyBaseLogger } from 'fastify';
import type { Db, Queryable } from '../db.js';

export interface JobRow {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  attempts: number;
  max_attempts: number;
}

export interface JobContext {
  attempt: number;
  isLastAttempt: boolean;
}

export type JobHandler = (payload: any, job: JobContext) => Promise<void>;

export async function enqueue(
  q: Queryable,
  type: string,
  payload: Record<string, unknown> = {},
  opts: { runAt?: Date; maxAttempts?: number } = {},
) {
  await q.query('INSERT INTO jobs (type, payload, run_at, max_attempts) VALUES ($1,$2,COALESCE($3, now()),$4)', [
    type,
    JSON.stringify(payload),
    opts.runAt ?? null,
    opts.maxAttempts ?? 5,
  ]);
}

const STALE_AFTER_SEC = 300;

/** Postgres-backed worker: claims jobs with FOR UPDATE SKIP LOCKED, retries with exponential backoff. */
export class Worker {
  private handlers = new Map<string, JobHandler>();
  private timer?: NodeJS.Timeout;
  private running = false;
  private inFlight?: Promise<unknown>;

  constructor(private db: Db, private log: FastifyBaseLogger, private pollMs = 1000) {}

  register(type: string, handler: JobHandler) {
    this.handlers.set(type, handler);
    return this;
  }

  /** Claims and runs one job. Returns false if the queue is empty. */
  async runOnce(): Promise<boolean> {
    await this.db.query(
      `UPDATE jobs SET status='queued' WHERE status='running' AND locked_at < now() - make_interval(secs => $1)`,
      [STALE_AFTER_SEC],
    );
    const { rows } = await this.db.query<JobRow>(
      `UPDATE jobs SET status='running', locked_at=now(), attempts=attempts+1
       WHERE id = (SELECT id FROM jobs WHERE status='queued' AND run_at <= now()
                   ORDER BY run_at, id FOR UPDATE SKIP LOCKED LIMIT 1)
       RETURNING id, type, payload, attempts, max_attempts`,
    );
    const job = rows[0];
    if (!job) return false;

    const handler = this.handlers.get(job.type);
    try {
      if (!handler) throw new Error(`no handler for job type "${job.type}"`);
      await handler(job.payload, { attempt: job.attempts, isLastAttempt: job.attempts >= job.max_attempts });
      // Payloads may carry one-time tokens (e.g. password reset mails): scrub on success.
      await this.db.query(`UPDATE jobs SET status='done', payload='{}', locked_at=NULL WHERE id=$1`, [job.id]);
    } catch (e) {
      const msg = (e as Error).message;
      this.log.error({ jobId: job.id, type: job.type, attempt: job.attempts, err: msg }, 'job failed');
      if (job.attempts >= job.max_attempts) {
        await this.db.query(`UPDATE jobs SET status='failed', last_error=$2, locked_at=NULL WHERE id=$1`, [job.id, msg]);
      } else {
        const delaySec = Math.min(3600, 5 * 2 ** (job.attempts - 1));
        await this.db.query(
          `UPDATE jobs SET status='queued', last_error=$2, locked_at=NULL, run_at = now() + make_interval(secs => $3) WHERE id=$1`,
          [job.id, msg, delaySec],
        );
      }
    }
    return true;
  }

  /** Runs jobs until none are due (or `max` is reached). */
  async drain(max = 100) {
    let n = 0;
    while (n < max && (await this.runOnce())) n++;
    return n;
  }

  start() {
    if (this.running) return;
    this.running = true;
    const tick = async () => {
      if (!this.running) return;
      try {
        this.inFlight = this.drain(20);
        await this.inFlight;
      } catch (e) {
        this.log.error({ err: (e as Error).message }, 'worker tick failed');
      }
      if (this.running) this.timer = setTimeout(tick, this.pollMs);
    };
    this.timer = setTimeout(tick, 0);
  }

  async stop() {
    this.running = false;
    if (this.timer) clearTimeout(this.timer);
    await this.inFlight?.catch(() => {});
  }
}
