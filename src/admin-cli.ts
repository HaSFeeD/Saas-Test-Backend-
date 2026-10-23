// Promote an existing user to admin: `npm run make-admin -- user@example.com`
import { loadConfig } from './config.js';
import { createDb } from './db.js';

const email = process.argv[2];
if (!email) {
  console.error('usage: npm run make-admin -- <email>');
  process.exit(1);
}
const db = createDb(loadConfig().DATABASE_URL);
const r = await db.query(`UPDATE users SET role='admin' WHERE lower(email)=lower($1) AND deleted_at IS NULL`, [email]);
console.log(r.rowCount ? `${email} is now an admin` : `no active user with email ${email}`);
await db.end();
