import { createHash } from 'node:crypto';
import type { ProviderMatch, SearchInput, SearchProvider } from './types.js';

const PLATFORMS = ['Tinder', 'Bumble', 'Hinge', 'Badoo', 'OkCupid', 'Plenty of Fish'];

/**
 * Deterministic fake data source for development and demos.
 * Same input => same output; produces no real people's data.
 * Names starting with "Nomatch" return nothing, "Boom" makes the provider throw.
 */
export class MockProvider implements SearchProvider {
  readonly name = 'mock';

  async search(input: SearchInput): Promise<ProviderMatch[]> {
    if (/^boom/i.test(input.firstName)) throw new Error('mock provider failure');
    if (/^nomatch/i.test(input.firstName)) return [];

    const seed = createHash('sha256')
      .update(`${input.firstName}|${input.lastName}|${input.city ?? ''}`.toLowerCase())
      .digest();
    const count = 1 + (seed[0]! % 4);
    const matches: ProviderMatch[] = [];
    for (let i = 0; i < count; i++) {
      const platform = PLATFORMS[seed[i + 1]! % PLATFORMS.length]!;
      const handle = `${input.firstName}${seed[i + 8]! % 100}`.toLowerCase();
      matches.push({
        platform,
        profileUrl: `https://example.com/mock/${platform.toLowerCase().replace(/\s/g, '-')}/${handle}`,
        displayName: `${input.firstName} ${input.lastName.charAt(0)}.`,
        username: handle,
        age: input.age ?? 20 + (seed[i + 16]! % 25),
        location: input.city ?? 'Unknown',
        photoUrl: `https://picsum.photos/seed/${seed.toString('hex').slice(i * 4, i * 4 + 8)}/400/400`,
        confidence: Math.round((0.55 + (seed[i + 20]! % 45) / 100) * 100) / 100,
        lastActiveAt: new Date(Date.now() - (seed[i + 24]! % 30) * 86_400_000),
        raw: { mock: true },
      });
    }
    return matches.sort((a, b) => b.confidence - a.confidence);
  }
}
