import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../src/speechloom/web', import.meta.url));

function files(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? files(join(directory, entry.name)) : [join(directory, entry.name)],
  );
}

test('nothing parses HTML or evaluates code', () => {
  const forbidden = /innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\s*\(|new Function\s*\(/;
  for (const file of files(root).filter((name) => /\.(js|mjs|html)$/.test(name))) {
    assert.doesNotMatch(readFileSync(file, 'utf8'), forbidden, file);
  }
});

test('the page has no inline scripts, styles or handlers', () => {
  const page = readFileSync(join(root, 'index.html'), 'utf8');
  assert.doesNotMatch(page, /<script(?![^>]*\bsrc=)/);
  assert.doesNotMatch(page, /<style/);
  assert.doesNotMatch(page, /\son[a-z]+\s*=/);
  assert.doesNotMatch(page, /\sstyle\s*=/);
});

test('no package.json exists in the repository tree above the web files', () => {
  let directory = root;
  for (let depth = 0; depth < 8; depth += 1) {
    assert.ok(!readdirSync(directory).includes('package.json'), directory);
    directory = join(directory, '..');
  }
});
