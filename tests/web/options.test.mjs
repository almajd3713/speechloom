import assert from 'node:assert/strict';
import { test } from 'node:test';

import { groupArtifacts, formatLabel } from '../../src/speechloom/web/js/artifacts.js';
import {
  FORMATS,
  addFiles,
  buildOptions,
  buildPathBody,
  isMedia,
  parsePaths,
  uploadLimitMessage,
} from '../../src/speechloom/web/js/options.js';

const file = (name, size = 10, lastModified = 1) => ({ name, size, lastModified });
const form = (overrides = {}) => ({ formats: [...FORMATS], diarize: false, translate: false, ...overrides });

test('the same file added twice is listed once', () => {
  const first = addFiles([], [file('a.mp4'), file('b.mp4')]);
  const second = addFiles(first.files, [file('a.mp4'), file('c.wav')]);

  assert.deepEqual(second.files.map((f) => f.name), ['a.mp4', 'b.mp4', 'c.wav']);
});

test('same name with different content is a different file', () => {
  const { files } = addFiles([], [file('a.mp4', 10), file('a.mp4', 11), file('a.mp4', 10, 2)]);
  assert.equal(files.length, 3);
});

test('non-media files are left out and reported', () => {
  const { files, ignored } = addFiles([], [file('notes.txt'), file('Clip.MP4')]);
  assert.deepEqual(files.map((f) => f.name), ['Clip.MP4']);
  assert.deepEqual(ignored, ['notes.txt']);
  assert.equal(isMedia('x.webm'), true);
  assert.equal(isMedia('x.exe'), false);
});

test('files over the upload limit are refused with both sizes and advice', () => {
  const files = [file('a.mp4', 3 * 1024 ** 3), file('b.mp4', 1024 ** 3)];
  const withPaths = uploadLimitMessage(files, 2 * 1024 ** 3, true);
  const without = uploadLimitMessage(files, 2 * 1024 ** 3, false);

  assert.match(withPaths, /4\.00 GB/);
  assert.match(withPaths, /2\.00 GB/);
  assert.match(withPaths, /--max-upload-mb/);
  assert.match(withPaths, /local paths/);
  assert.doesNotMatch(without, /local paths/);
  assert.equal(uploadLimitMessage([file('a', 5)], 5, false), null);
  assert.equal(uploadLimitMessage(files, 0, false), null);
});

test('all four formats are always sent explicitly, in a stable order', () => {
  assert.deepEqual(buildOptions(form()).options, { formats: ['txt', 'srt', 'vtt', 'json'] });
  assert.deepEqual(buildOptions(form({ formats: ['json', 'srt'] })).options.formats, ['srt', 'json']);
  assert.match(buildOptions(form({ formats: [] })).error, /format/);
});

test('translation needs two different languages', () => {
  assert.match(buildOptions(form({ translate: true })).error, /both/);
  assert.match(buildOptions(form({ translate: true, sourceLanguage: 'en', targetLanguage: 'en' })).error, /different/);
  const { options } = buildOptions(form({ translate: true, sourceLanguage: 'ru', targetLanguage: 'en' }));
  assert.equal(options.source_language, 'ru');
  assert.equal(options.translate_to, 'en');
  assert.equal('source_language' in buildOptions(form()).options, false);
});

test('speaker labels and advanced options', () => {
  const { options } = buildOptions(form({
    diarize: true,
    advanced: { audioStream: '1', workers: '2', resume: false, force: true, keepAudio: true, failFast: true },
  }));
  assert.deepEqual(options, {
    formats: ['txt', 'srt', 'vtt', 'json'], diarize: true, audio_stream: 1, workers: 2,
    force: true, keep_audio: true, fail_fast: true, resume: false,
  });
  assert.match(buildOptions(form({ advanced: { workers: '9' } })).error, /Workers/);
  assert.match(buildOptions(form({ advanced: { audioStream: '-1' } })).error, /audio stream/);
  assert.equal('workers' in buildOptions(form({ advanced: { workers: '' } })).options, false);
});

test('local paths', () => {
  assert.deepEqual(parsePaths(' /a/b.mp4 \n\n/c\n'), ['/a/b.mp4', '/c']);
  assert.deepEqual(buildPathBody(['/a'], { formats: ['txt'] }, true), { inputs: ['/a'], formats: ['txt'], recursive: true });
  assert.equal('recursive' in buildPathBody(['/a'], {}, false), false);
});

test('single-file jobs keep plain artifact names', () => {
  const [group] = groupArtifacts([{ name: 'srt' }, { name: 'edited.srt' }], 1);
  assert.deepEqual(group.items.map((i) => i.label), ['srt', 'edited.srt']);
});

test('multi-file artifacts group by position even when an earlier file has none', () => {
  const groups = groupArtifacts([{ name: '2.srt' }, { name: '2.txt' }, { name: '3.srt' }, { name: 'stray' }, { name: '9.srt' }], 3);

  assert.deepEqual(groups.map((g) => g.items.map((i) => i.label)), [[], ['srt', 'txt'], ['srt']]);
  assert.deepEqual(groups.map((g) => g.result), [1, 2, 3]);
});

test('artifact labels', () => {
  assert.equal(formatLabel('srt'), 'SRT subtitles');
  assert.equal(formatLabel('edited.txt'), 'Plain text (edited)');
  assert.equal(formatLabel('manifest'), 'manifest');
});
