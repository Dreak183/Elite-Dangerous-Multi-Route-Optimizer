import { readFileSync } from 'node:fs';
import { cloudflareTest } from '@cloudflare/vitest-pool-workers';
import { defineConfig } from 'vitest/config';

// Read with JSON.parse, not `import`: the bundler that loads this config rejects the lone-surrogate
// escapes in the shared vectors, which JSON.parse accepts.
const vectors = JSON.parse(readFileSync(new URL('./test/vectors.json', import.meta.url), 'utf8'));

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: './wrangler.toml' },
      miniflare: {
        bindings: { CREW_LEADERS: vectors.keys.leader.author, SOURCE_URL: 'https://example.invalid/edmrn' },
      },
    }),
  ],
});
