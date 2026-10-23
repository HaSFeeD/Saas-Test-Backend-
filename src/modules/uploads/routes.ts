import { randomUUID } from 'node:crypto';
import type { FastifyPluginAsyncZod } from 'fastify-type-provider-zod';
import type { Ctx } from '../../context.js';
import { sha256 } from '../../lib/crypto.js';
import { badRequest, notFound } from '../../lib/errors.js';
import { bearer, IdParams } from '../../lib/schemas.js';
import { authUser } from '../../plugins/auth.js';

/** Trust file signatures, not client-supplied Content-Type. */
export function sniffImage(buf: Buffer): { mime: string; ext: string } | null {
  if (buf.length >= 3 && buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff) return { mime: 'image/jpeg', ext: 'jpg' };
  if (buf.length >= 8 && buf.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])))
    return { mime: 'image/png', ext: 'png' };
  if (buf.length >= 12 && buf.toString('ascii', 0, 4) === 'RIFF' && buf.toString('ascii', 8, 12) === 'WEBP')
    return { mime: 'image/webp', ext: 'webp' };
  return null;
}

export const uploadRoutes =
  (ctx: Ctx): FastifyPluginAsyncZod =>
  async (app) => {
    const { db, storage } = ctx;
    app.addHook('onRequest', app.authenticate);

    app.post(
      '/',
      { schema: { tags: ['uploads'], security: bearer, consumes: ['multipart/form-data'], description: 'Upload a photo (field "file"; JPEG/PNG/WebP).' } },
      async (req, reply) => {
        const user = authUser(req);
        const file = await req.file();
        if (!file) throw badRequest('no_file', 'Send the image in a multipart field named "file"');
        let data: Buffer;
        try {
          data = await file.toBuffer();
        } catch {
          throw badRequest('file_too_large', `Max upload size is ${ctx.cfg.MAX_UPLOAD_MB} MB`);
        }
        const kind = sniffImage(data);
        if (!kind) throw badRequest('unsupported_type', 'Only JPEG, PNG and WebP images are accepted');

        const key = `${user.id}/${randomUUID()}.${kind.ext}`;
        await storage.put(key, data);
        const r = await db.query(
          `INSERT INTO uploads (user_id, storage_key, mime, size, sha256) VALUES ($1,$2,$3,$4,$5)
           RETURNING id, mime, size, created_at AS "createdAt"`,
          [user.id, key, kind.mime, data.length, sha256(data)],
        );
        return reply.code(201).send(r.rows[0]);
      },
    );

    app.get('/:id', { schema: { tags: ['uploads'], security: bearer, params: IdParams } }, async (req, reply) => {
      const r = await db.query('SELECT storage_key, mime FROM uploads WHERE id=$1 AND user_id=$2', [
        req.params.id,
        authUser(req).id,
      ]);
      const up = r.rows[0];
      if (!up) throw notFound();
      return reply.header('Content-Type', up.mime).header('Cache-Control', 'private, max-age=300').send(await storage.get(up.storage_key));
    });
  };
