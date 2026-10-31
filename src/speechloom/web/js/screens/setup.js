import { h, replace } from '../dom.js';
import { isSetupEnd } from '../events.js';
import { BACKENDS, FEATURES, checkTone, logLine, summarize } from '../setup-view.js';

export function mount(container, { api, capabilities, refreshCapabilities }) {
  const controller = new AbortController();
  let stopped = false;
  let running = false;
  let following = false;

  const summaryBox = h('div', { class: 'card' });
  const message = h('p', { role: 'alert', class: 'form-message' });
  const log = h('pre', { class: 'log', hidden: true, 'aria-live': 'polite' });
  const runLine = h('p', { class: 'muted', role: 'status' });
  const cancelButton = h('button', { type: 'button', hidden: true, onclick: cancel }, 'Cancel setup');
  const backend = h('select', { 'aria-label': 'Backend' }, BACKENDS.map((name) => h('option', { value: name }, name === 'auto' ? 'Automatic (recommended)' : name.toUpperCase())));
  const boxes = FEATURES.map((feature) => ({ feature, input: h('input', { type: 'checkbox', value: feature.name }) }));
  const fromSource = h('input', { type: 'checkbox' });
  const keepCache = h('input', { type: 'checkbox' });
  const start = h('button', { type: 'submit', class: 'primary' }, 'Install or update');
  const doctorButton = h('button', { type: 'button', onclick: runDoctor }, 'Run diagnostics');
  const doctorBox = h('div');

  const lastStatus = { value: null };
  const paintSummary = () => {
    const { headline, notes, tone } = summarize(lastStatus.value, capabilities.get());
    const status = lastStatus.value;
    replace(summaryBox,
      h('strong', { class: `tone-${tone}` }, headline),
      notes.map((note) => h('p', { class: 'muted' }, note)),
      status && status.installed ? h('dl', { class: 'facts' },
        h('dt', {}, 'Backend'), h('dd', {}, status.backend ?? 'unknown'),
        h('dt', {}, 'Features'), h('dd', {}, status.features.length ? status.features.join(', ') : 'speech recognition only'),
        h('dt', {}, 'Models'), h('dd', {}, status.models.length ? status.models.join(', ') : 'none')) : null);
  };
  const unsubscribe = capabilities.subscribe(paintSummary);

  function setRunning(value) {
    running = value;
    start.disabled = value;
    doctorButton.disabled = value;
    cancelButton.hidden = !value;
    if (value) replace(doctorBox);
  }

  async function loadState() {
    try {
      const state = await api.setupState();
      if (stopped) return;
      lastStatus.value = state.status;
      paintSummary();
      if (state.task && state.task.state === 'running') follow(state.task);
      else if (state.task) replace(runLine, `Last run ${state.task.state}${state.task.message ? `: ${state.task.message}` : ''}.`);
    } catch (error) {
      if (!stopped) replace(message, error.message);
    }
  }

  async function follow(task) {
    if (following) return;
    following = true;
    setRunning(true);
    log.hidden = false;
    log.textContent = '';
    replace(runLine, 'Setup is running. Downloads can take several minutes.');
    lastStatus.value = null;
    paintSummary();
    try {
      await api.streamSetupEvents({
        signal: controller.signal,
        isDone: isSetupEnd,
        onEvent: (event) => {
          log.append(`${logLine(event)}\n`);
          log.scrollTop = log.scrollHeight;
        },
      });
    } catch (error) {
      if (!stopped) replace(message, error.message);
    }
    following = false;
    if (stopped) return;
    setRunning(false);
    await refreshCapabilities(); // the server reloaded its settings before it reported the end
    replace(runLine, 'Setup finished. Checking the installation…');
    await loadState();
  }

  async function submit(event) {
    event.preventDefault();
    replace(message, '');
    try {
      const task = await api.startSetup({
        backend: backend.value,
        features: boxes.filter((box) => box.input.checked).map((box) => box.feature.name),
        from_source: fromSource.checked,
        keep_cache: keepCache.checked,
      });
      follow(task);
    } catch (error) {
      replace(message, error.message);
    }
  }

  async function cancel() {
    try { await api.cancelSetup(); replace(runLine, 'Cancelling…'); } catch (error) { replace(message, error.message); }
  }

  async function runDoctor() {
    replace(doctorBox, h('p', { class: 'muted' }, 'Running diagnostics…'));
    doctorButton.disabled = true;
    try {
      const report = await api.doctor();
      replace(doctorBox,
        h('p', {}, h('strong', { class: report.ready ? 'tone-ok' : 'tone-error' }, report.ready ? 'Everything needed is in place.' : 'Something needs attention.')),
        h('table', {}, h('tbody', {}, report.checks.map((check) => h('tr', {},
          h('td', {}, check.name),
          h('td', { class: `tone-${checkTone(check.status)}` }, check.status.toUpperCase()),
          h('td', {}, check.message))))));
    } catch (error) {
      replace(doctorBox, h('p', { class: 'error-text', role: 'alert' }, error.message));
    }
    doctorButton.disabled = running;
  }

  const form = h('form', { onsubmit: submit },
    h('div', { class: 'row' }, h('label', {}, 'Backend ', backend)),
    h('div', { class: 'row' }, boxes.map(({ feature, input }) => h('label', {}, input, ` ${feature.label} `, h('span', { class: 'muted' }, `(${feature.detail})`)))),
    h('details', {}, h('summary', {}, 'Advanced'),
      h('div', { class: 'row' }, h('label', {}, fromSource, ' Build the engine from source (needs a compiler)')),
      h('div', { class: 'row' }, h('label', {}, keepCache, ' Keep download caches'))),
    h('div', { class: 'row' }, start, cancelButton),
    message, runLine, log);

  replace(container,
    h('h1', {}, 'Setup'),
    summaryBox,
    h('h2', {}, 'Install or update'),
    h('p', { class: 'muted' }, 'Features add up: installing one keeps the others you already have. Setup can’t start while jobs are running.'),
    form,
    h('h2', {}, 'Diagnostics'),
    h('p', { class: 'muted' }, 'Checks the engine, models, tools and disk space.'),
    h('p', {}, doctorButton),
    doctorBox);
  paintSummary();
  loadState();
  return () => { stopped = true; unsubscribe(); controller.abort(); };
}
