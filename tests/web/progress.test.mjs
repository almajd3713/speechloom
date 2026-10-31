import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createJobTracker } from '../../src/speechloom/web/js/progress.js';

const T0 = Date.parse('2026-01-01T00:00:00Z');
const at = (seconds) => new Date(T0 + seconds * 1000).toISOString();
const ev = (source, stage, status, seconds, extra = {}) => ({
  source, stage, status, message: `${stage} ${status}`, timestamp: at(seconds), ...extra,
});
const now = (seconds) => T0 + seconds * 1000;

test('one file: the bar follows the estimate and holds at 99 % past it', () => {
  const tracker = createJobTracker();
  tracker.apply(ev('a.wav', 'transcribing', 'started', 0, { estimated_seconds: 10 }));

  assert.equal(tracker.bar(now(5)).fraction, 0.5);
  const late = tracker.bar(now(10));
  assert.equal(late.fraction, 0.99);
  assert.equal(late.overdue, true);
  assert.equal(tracker.bar(now(600)).fraction, 0.99);
  assert.equal(tracker.bar(now(600)).overdue, true);
  assert.equal(tracker.bar(now(9)).overdue, false);
});

test('the bar moves every second', () => {
  const tracker = createJobTracker();
  tracker.apply(ev('a.wav', 'transcribing', 'started', 0, { estimated_seconds: 100 }));
  const fractions = [1, 2, 3, 4].map((second) => tracker.bar(now(second)).fraction);
  assert.deepEqual(fractions, [0.01, 0.02, 0.03, 0.04]);
});

test('files that start together share one bar with the summed estimate', () => {
  const tracker = createJobTracker();
  tracker.apply(ev('a.wav', 'transcribing', 'started', 0, { estimated_seconds: 10 }));
  tracker.apply(ev('b.wav', 'transcribing', 'started', 0, { estimated_seconds: 30 }));

  const bar = tracker.bar(now(20));
  assert.equal(bar.estimateSeconds, 40);
  assert.equal(bar.fraction, 0.5);
  assert.equal(bar.files, 2);

  tracker.apply(ev('a.wav', 'transcribing', 'completed', 21));
  assert.notEqual(tracker.bar(now(22)), null);
  tracker.apply(ev('b.wav', 'transcribing', 'completed', 22));
  assert.equal(tracker.bar(now(23)), null);
});

test('without an estimate the bar is indeterminate and shows elapsed time', () => {
  const tracker = createJobTracker();
  tracker.apply(ev('a.wav', 'transcribing', 'started', 0, { estimated_seconds: null }));

  const bar = tracker.bar(now(42));
  assert.equal(bar.indeterminate, true);
  assert.equal(bar.fraction, null);
  assert.equal(bar.elapsedSeconds, 42);
  assert.equal(bar.overdue, false);
});

test('one file without an estimate makes the shared bar indeterminate', () => {
  const tracker = createJobTracker();
  tracker.apply(ev('a.wav', 'transcribing', 'started', 0, { estimated_seconds: 10 }));
  tracker.apply(ev('b.wav', 'transcribing', 'started', 0, {}));

  assert.equal(tracker.bar(now(5)).indeterminate, true);
});

test('a file that fails mid-run leaves the others on the bar', () => {
  const tracker = createJobTracker();
  tracker.apply(ev('a.wav', 'transcribing', 'started', 0, { estimated_seconds: 10 }));
  tracker.apply(ev('b.wav', 'transcribing', 'started', 0, { estimated_seconds: 10 }));
  tracker.apply(ev('a.wav', 'failed', 'failed', 4));

  assert.notEqual(tracker.bar(now(5)), null);
  assert.equal(tracker.files()[0].state, 'failed');
  tracker.apply(ev('b.wav', 'transcribing', 'completed', 8));
  assert.equal(tracker.bar(now(9)), null);
});

test('when every transcribing file fails the bar ends at once', () => {
  const tracker = createJobTracker();
  tracker.apply(ev('a.wav', 'transcribing', 'started', 0, { estimated_seconds: 10 }));
  tracker.apply(ev('a.wav', 'failed', 'failed', 1));

  assert.equal(tracker.bar(now(2)), null);
});

test('cancelled files leave the bar too', () => {
  const tracker = createJobTracker();
  tracker.apply(ev('a.wav', 'transcribing', 'started', 0, { estimated_seconds: 10 }));
  tracker.apply(ev('a.wav', 'cancelled', 'cancelled', 1));

  assert.equal(tracker.bar(now(2)), null);
});

test('files transcribed one after another each get their own bar', () => {
  const tracker = createJobTracker();
  tracker.apply(ev('a.wav', 'transcribing', 'started', 0, { estimated_seconds: 10 }));
  tracker.apply(ev('a.wav', 'transcribing', 'completed', 10));
  assert.equal(tracker.bar(now(11)), null);

  tracker.apply(ev('b.wav', 'transcribing', 'started', 12, { estimated_seconds: 20 }));
  const bar = tracker.bar(now(22));
  assert.equal(bar.estimateSeconds, 20);
  assert.equal(bar.fraction, 0.5);
});

test('job-level events and unknown timestamps do not break the tracker', () => {
  const tracker = createJobTracker();
  tracker.apply({ source: null, stage: 'completed', status: 'completed', message: 'Managed job completed' });
  tracker.apply({ source: 'a.wav', stage: 'transcribing', status: 'started', message: '', timestamp: 'garbage', estimated_seconds: 10 }, now(3));

  assert.equal(tracker.files().length, 1);
  assert.equal(tracker.bar(now(8)).fraction, 0.5);
});

test('stage rows show done, active and pending steps; translating only when it happens', () => {
  const tracker = createJobTracker();
  for (const stage of ['validating', 'probing']) {
    tracker.apply(ev('a.wav', stage, 'started', 0));
    tracker.apply(ev('a.wav', stage, 'completed', 1));
  }
  tracker.apply(ev('a.wav', 'normalizing', 'started', 2));

  const [file] = tracker.files();
  const byStage = Object.fromEntries(file.stages.map((s) => [s.stage, s.status]));
  assert.deepEqual(byStage, {
    validating: 'done', probing: 'done', normalizing: 'active', transcribing: 'pending', rendering: 'pending',
  });

  tracker.apply(ev('a.wav', 'translating', 'started', 3));
  assert.equal(tracker.files()[0].stages.find((s) => s.stage === 'translating').status, 'active');
});

test('a finished file reads completed', () => {
  const tracker = createJobTracker();
  tracker.apply(ev('a.wav', 'transcribing', 'started', 0, { estimated_seconds: 1 }));
  tracker.apply(ev('a.wav', 'completed', 'completed', 2));

  assert.equal(tracker.files()[0].state, 'completed');
  assert.equal(tracker.bar(now(3)), null);
});
