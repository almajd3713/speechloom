// Turns job events into per-file stage progress and the shared estimated bar. No DOM access.

export const STAGES = [
  'validating',
  'probing',
  'normalizing',
  'transcribing',
  'translating',
  'rendering',
];
const ALWAYS_SHOWN = new Set(['validating', 'probing', 'normalizing', 'transcribing', 'rendering']);
export const OVERDUE_FRACTION = 0.99;

const timeOf = (event, fallback) => {
  const parsed = Date.parse(event.timestamp);
  return Number.isNaN(parsed) ? fallback : parsed;
};

export function createJobTracker() {
  const files = new Map(); // source name -> record, in first-seen order
  let bar = null; // the group of files transcribing right now, or null

  function file(name) {
    if (!files.has(name)) {
      files.set(name, { name, state: 'running', current: null, done: new Set(), message: '', error: null });
    }
    return files.get(name);
  }

  function leaveBar(record) {
    if (!bar || !bar.members.has(record.name)) return;
    bar.members.get(record.name).transcribing = false;
    if (![...bar.members.values()].some((member) => member.transcribing)) bar = null;
  }

  return {
    apply(event, nowMs = Date.now()) {
      if (event.source == null) return;
      const record = file(event.source);
      record.message = event.message ?? '';
      if (event.stage === 'queued') return;
      if (event.stage === 'completed') {
        record.state = 'completed';
        record.current = null;
        leaveBar(record);
        return;
      }
      if (event.stage === 'failed' || event.stage === 'cancelled' || event.stage === 'interrupted') {
        record.state = event.stage;
        record.error = event.message ?? null;
        record.current = null;
        leaveBar(record);
        return;
      }
      if (event.status === 'started') {
        record.current = event.stage;
        if (event.stage === 'transcribing') {
          const startedAt = timeOf(event, nowMs);
          if (!bar) bar = { startedAt, members: new Map() };
          bar.members.set(record.name, {
            transcribing: true,
            estimate: typeof event.estimated_seconds === 'number' ? event.estimated_seconds : null,
          });
        }
      } else if (event.status === 'completed') {
        record.done.add(event.stage);
        if (record.current === event.stage) record.current = null;
        if (event.stage === 'transcribing') leaveBar(record);
      }
    },

    files() {
      return [...files.values()].map((record) => ({
        name: record.name,
        state: record.state,
        message: record.message,
        error: record.error,
        stages: STAGES.filter(
          (stage) => ALWAYS_SHOWN.has(stage) || record.done.has(stage) || record.current === stage,
        ).map((stage) => ({
          stage,
          status: record.done.has(stage)
            ? 'done'
            : record.current === stage
              ? record.state === 'running' ? 'active' : record.state
              : 'pending',
        })),
      }));
    },

    // The shared bar: null when no file is transcribing.
    bar(nowMs = Date.now()) {
      if (!bar) return null;
      const elapsed = Math.max(0, (nowMs - bar.startedAt) / 1000);
      const members = [...bar.members.values()];
      const known = members.every((member) => member.estimate !== null);
      if (!known) {
        return { indeterminate: true, fraction: null, elapsedSeconds: elapsed, estimateSeconds: null, overdue: false, files: members.length };
      }
      const estimate = members.reduce((sum, member) => sum + member.estimate, 0);
      const overdue = elapsed >= estimate;
      const fraction = overdue ? OVERDUE_FRACTION : Math.min(OVERDUE_FRACTION, elapsed / estimate);
      return { indeterminate: false, fraction, elapsedSeconds: elapsed, estimateSeconds: estimate, overdue, files: members.length };
    },
  };
}
