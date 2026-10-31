import assert from 'node:assert/strict';
import { test } from 'node:test';

import { matches } from '../../src/speechloom/web/js/editor/find.js';
import { currentRow } from '../../src/speechloom/web/js/editor/playback.js';
import { createSaveQueue } from '../../src/speechloom/web/js/editor/queue.js';
import { buildRows, currentEdits, speakerLabel } from '../../src/speechloom/web/js/editor/rows.js';
import { collapse, codePointLength, judgeEdit } from '../../src/speechloom/web/js/editor/text.js';

const NBSP = String.fromCodePoint(0xa0);
const EM_SPACE = String.fromCodePoint(0x2003);
const IDEOGRAPHIC = String.fromCodePoint(0x3000);
const NARROW_NBSP = String.fromCodePoint(0x202f);
const NEL = String.fromCodePoint(0x85); // a space in Python, not in JS \s
const FS = String.fromCodePoint(0x1c); // likewise
const BOM = String.fromCodePoint(0xfeff); // a space in JS \s, not in Python

test('collapse matches Python str.split()', () => {
  assert.equal(collapse('  a \t b\nc  '), 'a b c');
  assert.equal(collapse(`a${NBSP}${NBSP}b`), 'a b'); // no-break space
  assert.equal(collapse(`a${EM_SPACE}b${IDEOGRAPHIC}c${NARROW_NBSP}d`), 'a b c d');
  assert.equal(collapse(`a${NEL}b${FS}c`), 'a b c');
  assert.equal(collapse(`a${BOM}b`), `a${BOM}b`);
  assert.equal(collapse(NBSP), '');
  assert.equal(collapse(''), '');
});

test('a line differing from the original only by no-break spaces counts as the original', () => {
  assert.deepEqual(judgeEdit(`hello${NBSP} world`, 'hello world'), { kind: 'restore', text: 'hello world' });
  assert.deepEqual(judgeEdit(' hello   world ', 'hello world'), { kind: 'restore', text: 'hello world' });
});

test('judging edits', () => {
  assert.deepEqual(judgeEdit('Hello  there', 'hello there'), { kind: 'set', text: 'Hello there' });
  assert.equal(judgeEdit('   ', 'x').kind, 'invalid');
  assert.equal(judgeEdit('', 'x').kind, 'invalid');
  assert.equal(judgeEdit('x'.repeat(1000), 'y').kind, 'set');
  assert.match(judgeEdit('x'.repeat(1001), 'y').error, /1000/);
});

test('the length limit counts code points, as Python does', () => {
  const emoji = '😀'.repeat(1000);
  assert.equal(emoji.length, 2000);
  assert.equal(codePointLength(emoji), 1000);
  assert.equal(judgeEdit(emoji, 'y').kind, 'set');
  assert.equal(judgeEdit(`${emoji}😀`, 'y').kind, 'invalid');
});

const segments = [
  { text: 'going along\nslushy roads', start: 0.2, end: 4, speaker: 0 },
  { text: 'second line', start: 4.5, end: 7, speaker: 1 },
  { text: 'third', start: 8, end: 9, speaker: null },
];

test('rows collapse the original and label speakers as the subtitles do', () => {
  const { rows } = buildRows({ segments });
  assert.deepEqual(rows.map((r) => r.text), ['going along slushy roads', 'second line', 'third']);
  assert.deepEqual(rows.map((r) => r.speaker), ['Speaker 0', 'Speaker 1', '']);
  assert.equal(rows.every((r) => !r.edited), true);
  assert.equal(speakerLabel('A'), 'Speaker A');
});

const edited = (sha, extra = {}) => ({
  schema_version: 1,
  source_transcript_sha256: sha,
  edited_indices: [1],
  segments: [
    { index: 0, text: 'ignored: not an edited index' },
    { index: 1, text: 'Second, corrected' },
    { index: 2, text: 'also ignored' },
  ],
  ...extra,
});

test('edits apply only against the current transcript', () => {
  const current = buildRows({ segments, edited: edited('abc'), transcriptSha: 'abc' }).rows;
  assert.equal(current[1].text, 'Second, corrected');
  assert.equal(current[1].edited, true);
  assert.equal(current[0].text, 'going along slushy roads');
  assert.equal(current[0].edited, false);

  const stale = buildRows({ segments, edited: edited('abc'), transcriptSha: 'different' }).rows;
  assert.equal(stale[1].edited, false);
  assert.equal(currentEdits(edited('abc', { schema_version: 2 }), 'abc').size, 0);
  assert.equal(currentEdits(edited('abc', { edited_indices: 'x' }), 'abc').size, 0);
  assert.equal(currentEdits(null, 'abc').size, 0);
  assert.equal(currentEdits(edited('abc'), null).size, 0);
});

test('translations show only when the counts match; otherwise a note explains', () => {
  const same = { segments: segments.map((s, i) => ({ text: `t${i}` })) };
  assert.deepEqual(buildRows({ segments, translation: same }).rows.map((r) => r.translation), ['t0', 't1', 't2']);

  const shorter = buildRows({ segments, translation: { segments: [{ text: 'only one' }] } });
  assert.equal(shorter.rows[0].translation, null);
  assert.match(shorter.note, /1 lines and the transcript has 3/);
  assert.equal(buildRows({ segments }).note, null);
});

test('find filters on text, translation and speaker, ignoring case', () => {
  const row = { text: 'Hello World', translation: 'Привет', speaker: 'Speaker 1' };
  assert.equal(matches(row, ''), true);
  assert.equal(matches(row, '  '), true);
  assert.equal(matches(row, 'WORLD'), true);
  assert.equal(matches(row, 'привет'), true);
  assert.equal(matches(row, 'speaker 1'), true);
  assert.equal(matches(row, 'nope'), false);
});

test('the current line follows playback time', () => {
  const rows = [{ start: 1, end: 3 }, { start: 5, end: 6 }, { start: 20, end: 21 }];
  assert.equal(currentRow(rows, 0), -1);
  assert.equal(currentRow(rows, 1), 0);
  assert.equal(currentRow(rows, 3.5), 0); // lingers through the gap
  assert.equal(currentRow(rows, 5.2), 1);
  assert.equal(currentRow(rows, 12), -1); // a long silence
  assert.equal(currentRow(rows, 20.5), 2);
  assert.equal(currentRow(rows, 500), -1);
  assert.equal(currentRow([], 1), -1);
  assert.equal(currentRow(rows, Number.NaN), -1);
});

// A manual clock stands in for setTimeout so the tests run instantly.
function clock() {
  let now = 0;
  const timers = new Map();
  let nextId = 1;
  return {
    setTimer: (fn, ms) => { const id = nextId++; timers.set(id, { fn, at: now + ms }); return id; },
    clearTimer: (id) => timers.delete(id),
    async advance(ms) {
      now += ms;
      for (const [id, t] of [...timers]) if (t.at <= now) { timers.delete(id); t.fn(); }
      await settle();
    },
    pendingTimers: () => timers.size,
  };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));

function deferred() {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

test('saves 800 ms after typing stops, as one request', async () => {
  const sent = [];
  const c = clock();
  const queue = createSaveQueue({ send: async (changes) => { sent.push(changes); }, ...c });

  queue.set(1, 'a');
  await c.advance(500);
  queue.set(1, 'ab'); // typing again restarts the wait
  queue.set(2, null);
  await c.advance(799);
  assert.equal(sent.length, 0);
  await c.advance(1);

  assert.deepEqual(sent, [[{ index: 1, text: 'ab' }, { index: 2, text: null }]]);
  assert.equal(queue.state(), 'saved');
});

test('only one save is in flight; changes typed meanwhile go in the next one', async () => {
  const first = deferred();
  const calls = [];
  const c = clock();
  const queue = createSaveQueue({
    send: (changes) => { calls.push(changes); return calls.length === 1 ? first.promise : Promise.resolve(); },
    ...c,
  });

  queue.set(0, 'one');
  await c.advance(800);
  assert.equal(queue.state(), 'saving');
  queue.set(0, 'two');
  queue.set(3, 'three');
  await c.advance(800); // the timer fires while the first save is still out
  assert.equal(calls.length, 1);

  first.resolve();
  await settle();
  await settle();

  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1], [{ index: 0, text: 'two' }, { index: 3, text: 'three' }]);
  assert.equal(queue.state(), 'saved');
});

test('a failed save keeps the edits, reports the error and retries on demand', async () => {
  const c = clock();
  let fail = true;
  const sent = [];
  const states = [];
  const queue = createSaveQueue({
    send: async (changes) => { sent.push(changes); if (fail) throw new Error('Job is not completed'); },
    onChange: (s) => states.push(s.state),
    ...c,
  });

  queue.set(4, 'kept');
  await c.advance(800);
  assert.equal(queue.state(), 'failed');
  assert.equal(queue.error, 'Job is not completed');
  assert.equal(queue.hasUnsaved(), true);
  assert.equal(c.pendingTimers(), 0); // no silent retry loop

  queue.set(5, 'typed after the failure');
  fail = false;
  await queue.retry();
  await settle();

  assert.deepEqual(sent.at(-1), [{ index: 4, text: 'kept' }, { index: 5, text: 'typed after the failure' }]);
  assert.equal(queue.state(), 'saved');
  assert.equal(queue.error, null);
  assert.ok(states.includes('failed'));
});

test('a failure does not overwrite what was typed while it was in flight', async () => {
  const c = clock();
  const out = deferred();
  const sent = [];
  const queue = createSaveQueue({
    send: (changes) => { sent.push(changes); return sent.length === 1 ? out.promise : Promise.resolve(); },
    ...c,
  });

  queue.set(1, 'old');
  await c.advance(800);
  queue.set(1, 'newer');
  out.reject(new Error('offline'));
  await settle();
  await settle();
  await queue.retry();
  await settle();

  assert.deepEqual(sent.at(-1), [{ index: 1, text: 'newer' }]);
});

test('flush sends pending edits at once and waits for the save in flight', async () => {
  const c = clock();
  const sent = [];
  const queue = createSaveQueue({ send: async (changes) => { sent.push(changes); }, ...c });

  queue.set(1, 'a');
  const ok = await queue.flush();

  assert.equal(ok, true);
  assert.equal(sent.length, 1);
  assert.equal(queue.hasUnsaved(), false);
  assert.equal(c.pendingTimers(), 0);
});

test('flush reports failure instead of hanging', async () => {
  const c = clock();
  const queue = createSaveQueue({ send: async () => { throw new Error('nope'); }, ...c });
  queue.set(1, 'a');

  assert.equal(await queue.flush(), false);
  assert.equal(queue.hasUnsaved(), true);
});

test('dropping a line that became invalid cancels its waiting save', async () => {
  const c = clock();
  const sent = [];
  const queue = createSaveQueue({ send: async (changes) => { sent.push(changes); }, ...c });

  queue.set(1, 'valid for a moment');
  queue.drop(1);
  await c.advance(2000);

  assert.equal(sent.length, 0);
  assert.equal(queue.state(), 'saved');
});
