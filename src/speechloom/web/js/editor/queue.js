// Autosave queue: debounces typing, keeps one save in flight, and keeps failed edits.
// No DOM access.

export const SAVE_DELAY_MS = 800;

export function createSaveQueue({
  send,
  delayMs = SAVE_DELAY_MS,
  setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = (handle) => clearTimeout(handle),
  onChange = () => {},
}) {
  let pending = new Map(); // index -> text | null, waiting to be sent
  let inflight = null; // promise of the save being sent
  let timer = null;
  let rerun = false;
  let error = null;

  const state = () => {
    if (error && !inflight) return 'failed';
    if (inflight) return 'saving';
    if (pending.size > 0 || timer !== null) return 'pending';
    return 'saved';
  };
  const notify = () => onChange({ state: state(), error, unsaved: pending.size + (inflight ? 1 : 0) });

  function run() {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
    if (inflight) {
      rerun = true;
      return inflight;
    }
    if (pending.size === 0) {
      notify();
      return Promise.resolve();
    }
    const changes = pending;
    pending = new Map();
    error = null;
    inflight = Promise.resolve()
      .then(() => send([...changes].map(([index, text]) => ({ index, text }))))
      .then(
        () => {
          inflight = null;
        },
        (failure) => {
          inflight = null;
          for (const [index, text] of changes) if (!pending.has(index)) pending.set(index, text);
          error = failure && failure.message ? failure.message : 'The save failed.';
        },
      )
      .then(() => {
        const again = rerun || (pending.size > 0 && timer === null && !error);
        rerun = false;
        notify();
        if (again && !error && pending.size > 0) return run();
        return undefined;
      });
    notify();
    return inflight;
  }

  function schedule() {
    if (timer !== null) clearTimer(timer);
    timer = setTimer(() => {
      timer = null;
      run();
    }, delayMs);
    notify();
  }

  return {
    // value: the new text, or null to restore the original
    set(index, value) {
      pending.set(index, value);
      schedule();
    },
    // The line holds something that can't be saved: forget what was waiting for it.
    drop(index) {
      pending.delete(index);
      if (pending.size === 0 && timer !== null) {
        clearTimer(timer);
        timer = null;
      }
      notify();
    },
    retry: () => run(),
    // Sends everything now and settles when nothing is left (or a save failed).
    async flush() {
      for (;;) {
        await run();
        if (inflight) {
          await inflight;
        }
        if (pending.size === 0 || error) return !error;
      }
    },
    hasUnsaved: () => pending.size > 0 || inflight !== null || timer !== null,
    state,
    get error() {
      return error;
    },
  };
}
