import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createSseParser } from '../../src/speechloom/web/js/sse.js';

function parse(chunks) {
  const events = [];
  const parser = createSseParser((event) => events.push(event));
  for (const chunk of chunks) parser.feed(chunk);
  return { events, parser };
}

test('parses id, event and data', () => {
  const { events, parser } = parse(['id: 4\nevent: transcribing\ndata: {"a":1}\n\n']);
  assert.deepEqual(events, [{ id: '4', event: 'transcribing', data: '{"a":1}' }]);
  assert.equal(parser.lastEventId, '4');
});

test('copes with chunks split anywhere', () => {
  const text = 'id: 1\nevent: queued\ndata: one\n\nid: 2\ndata: two\n\n';
  for (let cut = 1; cut < text.length; cut += 1) {
    const { events } = parse([text.slice(0, cut), text.slice(cut)]);
    assert.deepEqual(
      events.map((event) => event.data),
      ['one', 'two'],
      `cut at ${cut}`,
    );
  }
});

test('chunks split anywhere in CRLF text', () => {
  const text = 'id: 1\r\ndata: one\r\n\r\nid: 2\r\ndata: two\r\n\r\n';
  for (let cut = 1; cut < text.length; cut += 1) {
    const { events } = parse([text.slice(0, cut), text.slice(cut)]);
    assert.deepEqual(events.map((event) => event.data), ['one', 'two'], `cut at ${cut}`);
  }
});

test('accepts CRLF, CR and a CRLF split between chunks', () => {
  assert.equal(parse(['data: a\r\n\r\n']).events[0].data, 'a');
  assert.equal(parse(['data: a\r\r']).events[0].data, 'a');
  assert.equal(parse(['data: a\r', '\n\r', '\n']).events.length, 1);
  assert.equal(parse(['data: a\r', '\ndata: b\r\n\r\n']).events[0].data, 'a\nb');
});

test('joins multi-line data and strips one leading space', () => {
  const { events } = parse(['data: first\ndata:  second\ndata\n\n']);
  assert.equal(events[0].data, 'first\n second\n');
});

test('ignores comments and events without data', () => {
  const { events } = parse([': keep-alive\n\n', 'event: lonely\n\n', 'data: x\n\n']);
  assert.deepEqual(events, [{ id: null, event: 'message', data: 'x' }]);
});

test('the id carries over until it changes, and unfinished events are dropped', () => {
  const { events, parser } = parse(['id: 7\ndata: a\n\n', 'data: b\n\n', 'data: partial']);
  assert.deepEqual(events.map((event) => event.id), ['7', '7']);
  parser.end();
  assert.equal(events.length, 2);
});

test('unicode survives', () => {
  assert.equal(parse(['data: Привет — 你好\n\n']).events[0].data, 'Привет — 你好');
});
