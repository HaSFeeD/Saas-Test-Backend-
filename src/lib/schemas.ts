import { z } from 'zod';

export const ErrorBody = z.object({
  error: z.object({ code: z.string(), message: z.string(), details: z.unknown().optional() }),
});

export const IdParams = z.object({ id: z.uuid() });

export const Pagination = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(20),
  offset: z.coerce.number().int().min(0).default(0),
});

export const bearer = [{ bearerAuth: [] }];
