import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  ApiError,
  NETWORK_MESSAGE,
  STREAM_LOST_MESSAGE,
  createClient,
  errorFromResponse,
} from '../../src/speechloom/web/js/api.js';
import { isJobEnd, isSetupEnd } from '../../src/speechloom/web/js/events.js';
import { createStore } from '../../src/speechloom/web/js/state.js';

const encoder = new TextEncoder();

function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function stream(chunks, { fail = false } = {}) {
  let index = 0;
  return new Response(
    new ReadableStream({
      pull(controller) {
        if (index < chunks.length) controller.enqueue(encoder.encode(chunks[index++]));
        else if (fail) controller.error(new TypeError('connection reset'));
        else controller.close();
      },
    }),
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
  );
}

const event = (id, data) => `id: ${id}\nevent: ${data.stage}\ndata: ${JSON.stringify(data)}\n\n`;

test('API errors keep the API wording', async () => {
  const client = createClient({
    fetchImpl: async () =>
      json(409, { error: { code: 'setup_running', message: 'A setup run is already in progress', request_id: 'r1' } }),
  });
  await assert.rejects(client.capabilities(), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.message, 'A setup run is already in progress');
    assert.equal(error.code, 'setup_running');
    assert.equal(error.status, 409);
    return true;
  });
});

test('non-JSON error bodies give a status message', () => {
  const error = errorFromResponse(502, 'Bad Gateway', '<html>nope</html>');
  assert.equal(error.message, 'The server answered 502 Bad Gateway');
});

test('network failures read "Cannot reach the Speechloom server"', async () => {
  const client = createClient({
    fetchImpl: async () => {
      throw new TypeError('Failed to fetch');
    },
  });
  await assert.rejects(client.health(), (error) => error.message === NETWORK_MESSAGE && error.code === 'network');
});

test('requests: methods, bodies and empty responses', async () => {
  const calls = [];
  const client = createClient({
    fetchImpl: async (path, init) => {
      calls.push([init.method, path, init.body]);
      return init.method === 'DELETE' ? new Response(null, { status: 204 }) : json(200, { items: [] });
    },
  });
  await client.listJobs({ offset: 50, limit: 25, state: 'failed' });
  assert.equal(await client.deleteJobFiles('a/b'), null);
  await client.saveEdits('j1', 2, [{ index: 0, text: null }]);
  await client.clearEdits('j1', 2);
  assert.deepEqual(calls, [
    ['GET', '/v1/jobs?offset=50&limit=25&state=failed', undefined],
    ['DELETE', '/v1/jobs/a%2Fb/files', undefined],
    ['PATCH', '/v1/jobs/j1/edits', '{"result":2,"segments":[{"index":0,"text":null}]}'],
    ['DELETE', '/v1/jobs/j1/edits?result=2', undefined],
  ]);
  assert.equal(client.mediaUrl('j1', 1), '/v1/jobs/j1/media');
  assert.equal(client.mediaUrl('j1', 3), '/v1/jobs/j1/media?result=3');
  assert.equal(client.artifactUrl('j1', '2.srt'), '/v1/jobs/j1/artifacts/2.srt');
});

test('streams resume with Last-Event-ID after a dropped connection', async () => {
  const seen = [];
  const requests = [];
  const answers = [
    () => stream([event(1, { stage: 'queued', source: 'a.wav' }), event(2, { stage: 'probing', source: 'a.wav' }).slice(0, 20)], { fail: true }),
    () => stream([event(2, { stage: 'probing', source: 'a.wav' }), event(3, { stage: 'completed', source: null })]),
  ];
  const client = createClient({
    fetchImpl: async (path, init) => {
      requests.push(init.headers['Last-Event-ID'] ?? null);
      return answers.shift()();
    },
    sleep: async () => {},
  });
  await client.streamJobEvents('j', { onEvent: (data) => seen.push(data.stage), isDone: isJobEnd });
  assert.deepEqual(seen, ['queued', 'probing', 'completed']);
  assert.deepEqual(requests, [null, '1']);
});

test('a stream that ends cleanly without an end event reconnects', async () => {
  let calls = 0;
  const client = createClient({
    fetchImpl: async () => {
      calls += 1;
      return calls === 1 ? stream([event(1, { stage: 'queued', source: 'a' })]) : stream([event(2, { stage: 'failed', source: null })]);
    },
    sleep: async () => {},
  });
  const stages = [];
  await client.streamJobEvents('j', { onEvent: (d) => stages.push(d.stage), isDone: isJobEnd });
  assert.deepEqual(stages, ['queued', 'failed']);
});

test('streams give up with a reload message after repeated failures', async () => {
  let calls = 0;
  const client = createClient({
    fetchImpl: async () => {
      calls += 1;
      throw new TypeError('offline');
    },
    sleep: async () => {},
  });
  await assert.rejects(
    client.streamSetupEvents({ onEvent() {}, isDone: isSetupEnd, maxFailures: 3 }),
    (error) => error.message === STREAM_LOST_MESSAGE,
  );
  assert.equal(calls, 4);
});

test('client errors end a stream at once', async () => {
  let calls = 0;
  const client = createClient({
    fetchImpl: async () => {
      calls += 1;
      return json(404, { error: { code: 'setup_not_started', message: 'No setup run has started' } });
    },
    sleep: async () => {},
  });
  await assert.rejects(client.streamSetupEvents({ onEvent() {}, isDone: isSetupEnd }), /No setup run/);
  assert.equal(calls, 1);
});

test('aborting stops a stream without an error', async () => {
  const controller = new AbortController();
  const client = createClient({
    fetchImpl: async (path, init) => {
      controller.abort();
      const error = new Error('aborted');
      error.name = 'AbortError';
      throw error;
    },
    sleep: async () => {},
  });
  await client.streamJobEvents('j', { onEvent() {}, isDone: isJobEnd, signal: controller.signal });
});

test('malformed event data is skipped', async () => {
  const seen = [];
  const client = createClient({
    fetchImpl: async () => stream(['id: 1\ndata: not json\n\n', event(2, { stage: 'completed', source: null })]),
    sleep: async () => {},
  });
  await client.streamJobEvents('j', { onEvent: (d) => seen.push(d.stage), isDone: isJobEnd });
  assert.deepEqual(seen, ['completed']);
});

test('job and setup end events', () => {
  assert.equal(isJobEnd({ stage: 'completed', source: null }), true);
  assert.equal(isJobEnd({ stage: 'completed', source: 'a.wav' }), false);
  assert.equal(isJobEnd({ stage: 'transcribing', source: null }), false);
  assert.equal(isJobEnd({ stage: 'interrupted', source: null }), true);
  assert.equal(isSetupEnd({ stage: 'cancelled' }), true);
  assert.equal(isSetupEnd({ stage: 'installing' }), false);
});

class FakeXhr {
  constructor() {
    this.upload = {};
    this.headers = {};
  }
  open(method, path) {
    this.method = method;
    this.path = path;
  }
  setRequestHeader(name, value) {
    this.headers[name] = value;
  }
  send(form) {
    this.form = form;
    queueMicrotask(() => {
      this.upload.onprogress({ lengthComputable: true, loaded: 5, total: 10 });
      this.status = this.answer.status;
      this.statusText = '';
      this.responseText = JSON.stringify(this.answer.body);
      this.onload();
    });
  }
}

test('uploads report progress and send files plus options', async () => {
  const xhr = new FakeXhr();
  xhr.answer = { status: 202, body: { id: 'j1' } };
  const client = createClient({ xhrFactory: () => xhr });
  const progress = [];
  const file = new File(['abc'], 'clip.wav');
  const job = await client.uploadJob([file], { formats: ['txt'] }, { onProgress: (l, t) => progress.push([l, t]) });
  assert.deepEqual(job, { id: 'j1' });
  assert.deepEqual(progress, [[5, 10]]);
  assert.equal(xhr.method, 'POST');
  assert.equal(xhr.path, '/v1/jobs');
  assert.equal(xhr.form.get('options'), '{"formats":["txt"]}');
  assert.equal(xhr.form.get('files').name, 'clip.wav');
});

test('upload errors keep the API wording and network errors are named', async () => {
  const refused = new FakeXhr();
  refused.answer = { status: 409, body: { error: { code: 'setup_running', message: 'Setup is running; try again when it ends' } } };
  await assert.rejects(createClient({ xhrFactory: () => refused }).uploadJob([], {}), /Setup is running/);

  const broken = new FakeXhr();
  broken.send = function () {
    queueMicrotask(() => this.onerror());
  };
  await assert.rejects(createClient({ xhrFactory: () => broken }).uploadJob([], {}), { message: NETWORK_MESSAGE });
});

test('store notifies subscribers until they unsubscribe', () => {
  const store = createStore(1);
  const seen = [];
  const off = store.subscribe((value) => seen.push(value));
  store.set(2);
  off();
  store.set(3);
  assert.deepEqual(seen, [2]);
  assert.equal(store.get(), 3);
});
