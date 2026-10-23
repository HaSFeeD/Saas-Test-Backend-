export interface SearchInput {
  firstName: string;
  lastName: string;
  age?: number;
  city?: string;
  region?: string;
  country?: string;
  email?: string;
  phone?: string;
  username?: string;
  photo?: { data: Buffer; mime: string };
}

export interface ProviderMatch {
  platform: string;
  profileUrl: string;
  displayName: string;
  username?: string;
  age?: number;
  location?: string;
  photoUrl?: string;
  /** 0..1 */
  confidence: number;
  lastActiveAt?: Date;
  raw?: unknown;
}

/**
 * Pluggable data source. Real implementations must come from a legally vetted,
 * licensed data source (see PLAN.md §7 / §8).
 */
export interface SearchProvider {
  readonly name: string;
  search(input: SearchInput, signal: AbortSignal): Promise<ProviderMatch[]>;
}
