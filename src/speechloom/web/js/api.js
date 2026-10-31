// Client for the local HTTP API. Takes fetch and XMLHttpRequest as parameters so Node can test it.

import { createSseParser } from './sse.js';

export const NETWORK_MESSAGE = 'Cannot reach the Speechloom server';
export const STREAM_LOST_MESSAGE =
  'Lost the live updates from the Speechloom server. Reload the page to continue.';

export class ApiError extends Error {
  constructor(message, { status = 0, code = 'unknown', details = null, requestId = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

export class NetworkError extends ApiError {
  constructor() {
    super(NETWORK_MESSAGE, { code: 'network' });
    this.name = 'NetworkError';
  }
}

// The API words its own errors; show them as written.
export function errorFromResponse(status, statusText, bodyText) {
  try {
    const body = JSON.parse(bodyText);
    if (body && body.error && typeof body.error.message === 'string') {
      const { code, message, details, request_id: requestId } = body.error;
      return new ApiError(message, { status, code, details, requestId });
    }
  } catch {
    // not JSON: fall through
  }
  return new ApiError(`The server answered ${status} ${statusText}`.trim(), { status });
}

export function createClient({
  fetchImpl = (...args) => globalThis.fetch(...args),
  xhrFactory = () => new XMLHttpRequest(),
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
  async function send(path, init = {}) {
    let response;
    try {
      response = await fetchImpl(path, init);
    } catch (error) {
      if (error && error.name === 'AbortError') throw error;
      throw new NetworkError();
    }
    return response;
  }

  async function request(method, path, { json, signal } = {}) {
    const init = { method, signal, headers: { Accept: 'application/json' } };
    if (json !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(json);
    }
    const response = await send(path, init);
    const text = await response.text();
    if (!response.ok) throw errorFromResponse(response.status, response.statusText, text);
    return text ? JSON.parse(text) : null;
  }

  const query = (params) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
    }
    const text = search.toString();
    return text ? `?${text}` : '';
  };
  const job = (id) => `/v1/jobs/${encodeURIComponent(id)}`;

  // Reads an event stream, reconnecting with Last-Event-ID until `isDone(data)` is true.
  async function streamEvents(path, { onEvent, isDone, signal, maxFailures = 5, retryMs = 1000 }) {
    let lastEventId = null;
    let failures = 0;
    let finished = false;
    while (!finished) {
      if (signal && signal.aborted) return;
      let broke = true;
      try {
        const headers = { Accept: 'text/event-stream' };
        if (lastEventId) headers['Last-Event-ID'] = lastEventId;
        const response = await send(path, { method: 'GET', headers, signal });
        if (!response.ok) {
          const error = errorFromResponse(response.status, response.statusText, await response.text());
          if (response.status < 500) throw error;
          throw new Error(error.message);
        }
        const decoder = new TextDecoder();
        const parser = createSseParser((event) => {
          if (event.id) lastEventId = event.id;
          let data;
          try {
            data = JSON.parse(event.data);
          } catch {
            return;
          }
          failures = 0;
          onEvent(data, event);
          if (isDone(data)) finished = true;
        });
        const reader = response.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          parser.feed(decoder.decode(value, { stream: true }));
        }
        parser.end();
        broke = !finished;
      } catch (error) {
        if (error && error.name === 'AbortError') return;
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) throw error;
      }
      if (finished) return;
      if (broke) failures += 1;
      if (failures > maxFailures) throw new ApiError(STREAM_LOST_MESSAGE, { code: 'stream_lost' });
      await sleep(retryMs);
    }
  }

  function uploadJob(files, options, { onProgress, signal } = {}) {
    return new Promise((resolve, reject) => {
      const xhr = xhrFactory();
      const form = new FormData();
      for (const file of files) form.append('files', file, file.name);
      form.append('options', JSON.stringify(options));
      xhr.open('POST', '/v1/jobs');
      xhr.setRequestHeader('Accept', 'application/json');
      xhr.upload.onprogress = (event) => {
        if (onProgress && event.lengthComputable) onProgress(event.loaded, event.total);
      };
      xhr.onerror = () => reject(new NetworkError());
      xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'));
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText));
        } else {
          reject(errorFromResponse(xhr.status, xhr.statusText, xhr.responseText));
        }
      };
      if (signal) signal.addEventListener('abort', () => xhr.abort(), { once: true });
      xhr.send(form);
    });
  }

  return {
    health: (signal) => request('GET', '/v1/health', { signal }),
    capabilities: () => request('GET', '/v1/capabilities'),
    storage: () => request('GET', '/v1/storage'),
    listJobs: ({ offset = 0, limit = 50, state } = {}) =>
      request('GET', `/v1/jobs${query({ offset, limit, state })}`),
    getJob: (id) => request('GET', job(id)),
    createJob: (body) => request('POST', '/v1/jobs', { json: body }),
    uploadJob,
    cancelJob: (id) => request('DELETE', job(id)),
    deleteJobFiles: (id) => request('DELETE', `${job(id)}/files`),
    deleteUpload: (id) => request('DELETE', `${job(id)}/upload`),
    listArtifacts: (id) => request('GET', `${job(id)}/artifacts`),
    artifactUrl: (id, name) => `${job(id)}/artifacts/${name.split('/').map(encodeURIComponent).join('/')}`,
    fetchArtifactText: async (id, name, signal) => {
      const url = `${job(id)}/artifacts/${name.split('/').map(encodeURIComponent).join('/')}`;
      const response = await send(url, { signal });
      const text = await response.text();
      if (!response.ok) throw errorFromResponse(response.status, response.statusText, text);
      return text;
    },
    mediaUrl: (id, result) => `${job(id)}/media${query({ result: result > 1 ? result : undefined })}`,
    saveEdits: (id, result, segments) =>
      request('PATCH', `${job(id)}/edits`, { json: { result, segments } }),
    clearEdits: (id, result) => request('DELETE', `${job(id)}/edits${query({ result })}`),
    streamJobEvents: (id, options) => streamEvents(`${job(id)}/events`, options),
    doctor: () => request('GET', '/v1/doctor'),
    setupState: () => request('GET', '/v1/setup'),
    startSetup: (body) => request('POST', '/v1/setup', { json: body ?? {} }),
    cancelSetup: () => request('DELETE', '/v1/setup'),
    streamSetupEvents: (options) => streamEvents('/v1/setup/events', options),
  };
}
