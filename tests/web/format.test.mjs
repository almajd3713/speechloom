import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  clamp,
  formatBytes,
  formatDuration,
  formatPercent,
  pluralize,
} from '../../src/speechloom/web/js/format.js';

test('formatBytes', () => {
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(1023), '1023 B');
  assert.equal(formatBytes(1536), '1.50 KB');
  assert.equal(formatBytes(15 * 1024 * 1024), '15.0 MB');
  assert.equal(formatBytes(4.4 * 1024 ** 3), '4.40 GB');
  assert.equal(formatBytes(-1), '–');
  assert.equal(formatBytes(Number.NaN), '–');
});

test('formatDuration', () => {
  assert.equal(formatDuration(0), '0:00');
  assert.equal(formatDuration(5.9), '0:05');
  assert.equal(formatDuration(75), '1:15');
  assert.equal(formatDuration(3725), '1:02:05');
  assert.equal(formatDuration(-1), '–');
  assert.equal(formatDuration(undefined), '–');
});

test('formatPercent clamps', () => {
  assert.equal(formatPercent(0.456), '46 %');
  assert.equal(formatPercent(2), '100 %');
  assert.equal(formatPercent(-1), '0 %');
});

test('clamp and pluralize', () => {
  assert.equal(clamp(5, 0, 3), 3);
  assert.equal(clamp(-5, 0, 3), 0);
  assert.equal(pluralize(1, 'file'), '1 file');
  assert.equal(pluralize(2, 'file'), '2 files');
});

test('displayName strips the upload prefix only for uploads', async () => {
  const { displayName } = await import('../../src/speechloom/web/js/format.js');
  assert.equal(displayName('001-clip.wav', true), 'clip.wav');
  assert.equal(displayName('001-clip.wav', false), '001-clip.wav');
  assert.equal(displayName('clip.wav', true), 'clip.wav');
});
