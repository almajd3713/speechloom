import { ApiError } from '../api.js';
import { h, replace } from '../dom.js';
import { displayName, formatBytes, formatPercent } from '../format.js';
import { jobHref } from '../router.js';

const ACTIVE = new Set(['queued', 'running']);

export function mount(container, { api }) {
  let stopped = false;
  const summary = h('div', { class: 'card' });
  const table = h('div');
  const message = h('p', { role: 'alert', class: 'form-message' });

  async function act(job, what) {
    const ask = what === 'upload'
      ? 'Delete the uploaded media for this job? The transcript and downloads are kept.'
      : 'Delete this job and its stored files? This can’t be undone.';
    if (!confirm(ask)) return;
    try {
      await (what === 'upload' ? api.deleteUpload(job.id) : api.deleteJobFiles(job.id));
      replace(message, '');
    } catch (error) {
      // Already gone is the outcome the user wanted.
      if (!(error instanceof ApiError && error.status === 404)) replace(message, error.message);
    }
    load();
  }

  function paint(report) {
    replace(summary,
      h('strong', {}, `${formatBytes(report.total_bytes)} used by Speechloom`), ' ',
      h('span', { class: 'muted' }, `· ${formatBytes(report.upload_bytes)} uploads · ${formatBytes(report.output_bytes)} results · ${formatBytes(report.free_bytes)} free on this disk`));
    if (report.jobs.length === 0) {
      replace(table, h('p', { class: 'muted' }, 'No jobs are using disk space.'));
      return;
    }
    replace(table, h('table', {},
      h('thead', {}, h('tr', {}, ['Job', 'Uploads', 'Results', 'Share', ''].map((t) => h('th', {}, t)))),
      h('tbody', {}, report.jobs.map((job) => {
        const active = ACTIVE.has(job.state);
        const fill = h('span');
        fill.style.width = `${Math.round(job.share * 100)}%`;
        return h('tr', {},
          h('td', {}, h('a', { href: jobHref(job.id) }, job.inputs.map((n) => displayName(n, job.upload_bytes > 0)).join(', ') || job.id.slice(0, 12)), ' ', h('span', { class: 'muted' }, job.state)),
          h('td', {}, formatBytes(job.upload_bytes)),
          h('td', {}, formatBytes(job.output_bytes)),
          h('td', {}, h('div', { class: 'bar', title: formatPercent(job.share) }, fill)),
          h('td', { class: 'actions' },
            job.upload_bytes > 0 ? h('button', { type: 'button', disabled: active, onclick: () => act(job, 'upload') }, 'Delete upload') : null,
            h('button', { type: 'button', class: 'danger', disabled: active, title: active ? 'Wait for the job to finish' : '', onclick: () => act(job, 'job') }, 'Delete job')));
      }))));
  }

  async function load() {
    try {
      const report = await api.storage();
      if (!stopped) paint(report);
    } catch (error) {
      if (!stopped) replace(message, error.message);
    }
  }

  replace(container, h('h1', {}, 'Storage'), summary, message, table);
  replace(summary, h('span', { class: 'muted' }, 'Measuring…'));
  load();
  return () => { stopped = true; };
}
