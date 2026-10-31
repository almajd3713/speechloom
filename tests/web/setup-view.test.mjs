import assert from 'node:assert/strict';
import { test } from 'node:test';

import { checkTone, logLine, summarize } from '../../src/speechloom/web/js/setup-view.js';

const caps = (transcription) => ({ features: { transcription } });
const status = (extra = {}) => ({ installed: true, ready: true, issues: [], features: [], models: [], ...extra });

test('while the status loads', () => {
  assert.equal(summarize(null, null).headline, 'Checking the installation…');
});

test('installed and ready', () => {
  assert.deepEqual(summarize(status(), caps(true)), { headline: 'Installed and ready', notes: [], tone: 'ok' });
});

test('installed but broken lists the issues', () => {
  const result = summarize(status({ ready: false, issues: ['runtime is missing or its checksum changed'] }), caps(false));
  assert.equal(result.headline, 'Installed, but needs repair');
  assert.deepEqual(result.notes, ['runtime is missing or its checksum changed']);
});

test('not installed and not ready asks for setup', () => {
  const result = summarize(status({ installed: false, ready: false }), caps(false));
  assert.equal(result.headline, 'Not installed yet');
  assert.equal(result.tone, 'warning');
});

test('not installed yet ready through a hand-written configuration does not contradict the banner', () => {
  const result = summarize(status({ installed: false, ready: false }), caps(true));
  assert.equal(result.headline, 'Using your own configuration');
  assert.equal(result.tone, 'ok');
  assert.match(result.notes[0], /transcription is ready/);
});

test('diagnostic tones and log lines', () => {
  assert.equal(checkTone('ok'), 'ok');
  assert.equal(checkTone('warn'), 'warning');
  assert.equal(checkTone('error'), 'error');
  assert.match(logLine({ message: 'installing ASR model', timestamp: '2026-01-01T00:00:00Z' }), /installing ASR model$/);
  assert.equal(logLine({ message: 'x', timestamp: 'bad' }), 'x');
});
