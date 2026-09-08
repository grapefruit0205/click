import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';

test('documented asset exists', () => {
  const guide = readFileSync(new URL('./guide.md', import.meta.url), 'utf8');
  assert.match(guide, /\.\.\/assets\/logo\.txt/);
  assert.equal(existsSync(new URL('../assets/logo.txt', import.meta.url)), true);
});
