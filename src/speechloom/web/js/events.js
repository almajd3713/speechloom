// Which progress events end a stream. No DOM access.

const JOB_END = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
const SETUP_END = new Set(['completed', 'failed', 'cancelled']);

// Per-file events carry a source; the job's own final event does not.
export function isJobEnd(data) {
  return data.source == null && JOB_END.has(data.stage);
}

export function isSetupEnd(data) {
  return SETUP_END.has(data.stage);
}
