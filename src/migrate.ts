import { loadConfig } from './config.js';
import { createDb, migrate } from './db.js';

const db = createDb(loadConfig().DATABASE_URL);
const applied = await migrate(db);
console.log(applied.length ? `applied: ${applied.join(', ')}` : 'database is up to date');
await db.end();
