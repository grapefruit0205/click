import { readFileSync } from 'node:fs';
import { test, expect } from 'vitest';
import { expectedSchemaVersion } from '../../frontend/value.js';

test('frontend agrees with shared schema', () => {
  const schema = JSON.parse(readFileSync(new URL('../../shared/api.json', import.meta.url)));
  expect(schema.version).toBe(expectedSchemaVersion);
});
