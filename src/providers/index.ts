import type { Config } from '../config.js';
import { MockProvider } from './mock.js';
import type { SearchProvider } from './types.js';

export function createSearchProvider(cfg: Config): SearchProvider {
  switch (cfg.SEARCH_PROVIDER) {
    case 'mock':
      if (cfg.NODE_ENV === 'production') throw new Error('The mock search provider must not be used in production');
      return new MockProvider();
    default:
      throw new Error(`Unknown SEARCH_PROVIDER "${cfg.SEARCH_PROVIDER}". Implement SearchProvider in src/providers and register it here.`);
  }
}
