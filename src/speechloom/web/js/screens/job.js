import { ApiError } from '../api.js';
import { groupArtifacts, formatLabel } from '../artifacts.js';
import { h, replace } from '../dom.js';
import { isJobEnd } from '../events.js';
import { displayName, formatBytes, formatDuration, formatPercent } from '../format.js';
import { createJobTracker } from '../progress.js';
import * as editor from '../editor/index.js';

const ACTIVE = new Set(['queued', 'running']);

export function mount(container, { api, route }) {
  const id = route.params.id;
  const controller = new AbortController();
  const tracker = createJobTracker();
  let job = null;
  let tick = null;
  let editorStop = null;
  let activeResult = 1;

  const show = (name) => displayName(name, job?.has_upload === true);
  const header = h('div');
  const bar = h('div', { class: 'card', hidden: true });
  const stages = h('div');
  const results = h('div');
  const editorBox = h('div');
  const errorBox = h('p', { role: 'alert' });
  const root = h('div', {}, header, errorBox, bar, stages, results, editorBox);

  const gone = () => {
    stopAll();
    replace(container, h('h1', {}, 'Job'), h('p', {}, 'This job no longer exists.'), h('p', {}, h('a', { href: '#/jobs' }, 'Back to Jobs')));
  };

  function renderBar() {
    const state = tracker.bar();
    bar.hidden = state === null;
    if (state === null) return;
    const fill = h('span');
    if (!state.indeterminate) fill.style.width = `${(state.fraction * 100).toFixed(1)}%`;
    const meter = h('div', { class: state.indeterminate ? 'bar indeterminate' : 'bar', role: 'progressbar', 'aria-label': 'Estimated transcription progress' }, fill);
    if (!state.indeterminate) meter.setAttribute('aria-valuenow', String(Math.round(state.fraction * 100)));
    replace(bar, h('strong', {}, 'Transcribing'), meter,
      h('p', { class: 'muted' }, state.indeterminate
        ? `Elapsed ${formatDuration(state.elapsedSeconds)}`
        : state.overdue
          ? `Taking longer than estimated · elapsed ${formatDuration(state.elapsedSeconds)}`
          : `${formatPercent(state.fraction)} · about ${formatDuration(Math.max(0, state.estimateSeconds - state.elapsedSeconds))} left (estimate)`));
  }

  function renderStages() {
    replace(stages, tracker.files().map((file) => h('div', { class: 'card' },
      h('strong', {}, show(file.name)), ' ', h('span', { class: 'muted' }, file.state),
      h('ol', { class: 'stages' }, file.stages.map((s) => h('li', { class: `stage-${s.status}` }, s.stage))),
      file.error ? h('p', { class: 'error-text' }, file.error) : null)));
  }

  function renderHeader() {
    const active = ACTIVE.has(job.state);
    replace(header, h('h1', {}, job.inputs.map(show).join(', ') || 'Job'),
      h('p', {}, h('span', { class: `state state-${job.state}` }, job.state), job.error ? ` · ${job.error}` : ''),
      active ? h('button', { type: 'button', onclick: cancel }, 'Cancel job') : null,
      !active ? h('button', { type: 'button', class: 'danger', onclick: remove }, 'Delete job…') : null);
  }

  async function cancel() {
    try { job = await api.cancelJob(id); renderHeader(); } catch (error) { fail(error); }
  }

  async function remove() {
    if (!confirm('Delete this job and its stored files? This can’t be undone.')) return;
    try { await api.deleteJobFiles(id); location.hash = '#/jobs'; } catch (error) { fail(error); }
  }

  function fail(error) {
    if (error instanceof ApiError && error.status === 404) return gone();
    replace(errorBox, error.message);
  }

  let paintDownloads = null;

  async function fetchGroups() {
    const items = (await api.listArtifacts(id)).items;
    return groupArtifacts(items, Math.max(1, job.results.length));
  }

  async function renderResults() {
    if (job.state !== 'completed' && job.state !== 'failed') { replace(results); return; }
    let groups;
    try { groups = await fetchGroups(); } catch (error) { return fail(error); }
    const panel = h('div');
    const tabs = h('div', { class: 'tabs', role: 'tablist' });
    const paint = (all) => {
      const group = all[activeResult - 1] ?? all[0];
      replace(panel, h('ul', {}, group.items.map((item) => h('li', {},
        h('a', { href: api.artifactUrl(id, item.name), download: item.filename }, formatLabel(item.label)), ' ',
        h('span', { class: 'muted' }, item.size == null ? '' : formatBytes(item.size))))));
      replace(tabs, all.length > 1 ? all.map((g) => h('button', { type: 'button', role: 'tab', 'aria-selected': String(g.result === activeResult), onclick: () => { activeResult = g.result; paint(all); mountEditor(); } }, show(job.results[g.result - 1]?.source ?? `File ${g.result}`))) : null);
    };
    paintDownloads = async () => { try { paint(await fetchGroups()); } catch { /* the next full load reports it */ } };
    paint(groups);
    replace(results, h('h2', {}, 'Downloads'), tabs, panel);
    mountEditor();
  }

  function mountEditor() {
    editorStop?.();
    editorStop = null;
    if (job.state !== 'completed') return;
    editorStop = editor.mount(editorBox, { api, jobId: id, result: activeResult, resultCount: Math.max(1, job.results.length), onSaved: () => paintDownloads?.() });
  }

  function stopAll() {
    controller.abort();
    clearInterval(tick);
    editorStop?.();
    editorStop = null;
  }

  async function start() {
    try { job = await api.getJob(id); } catch (error) { return fail(error); }
    renderHeader();
    tick = setInterval(renderBar, 1000);
    try {
      await api.streamJobEvents(id, {
        signal: controller.signal,
        isDone: isJobEnd,
        onClosed: async () => !ACTIVE.has((await api.getJob(id)).state),
        onEvent: (data) => { tracker.apply(data); renderStages(); renderBar(); },
      });
    } catch (error) { return fail(error); }
    if (controller.signal.aborted) return;
    clearInterval(tick);
    renderBar();
    try { job = await api.getJob(id); } catch (error) { return fail(error); }
    renderHeader();
    renderStages();
    await renderResults();
  }

  replace(container, root);
  start();
  return stopAll;
}
