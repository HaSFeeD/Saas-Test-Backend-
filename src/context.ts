import type { Config } from './config.js';
import type { Db } from './db.js';
import type { Worker } from './jobs/queue.js';
import type { BillingProvider } from './modules/billing/provider.js';
import type { Mailer } from './lib/mailer.js';
import type { Storage } from './lib/storage.js';
import type { SearchProvider } from './providers/types.js';

/** Everything routes and jobs depend on. Built once in buildApp; any part can be swapped via overrides. */
export interface Ctx {
  cfg: Config;
  db: Db;
  mailer: Mailer;
  storage: Storage;
  billing: BillingProvider;
  searchProvider: SearchProvider;
  worker: Worker;
}
