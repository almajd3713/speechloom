import { h, replace } from '../dom.js';
import { displayName, formatDateTime } from '../format.js';
import { jobHref } from '../router.js';

const STATES = ['', 'queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted'];
const PAGE = 20;
const ACTIVE_MS = 2_000;
const IDLE_MS = 10_000;

export function mount(container, { api }) {
  let offset = 0;
  let state = '';
  let timer = null;
  let stopped = false;
  let token = 0;

  const body = h('tbody');
  const note = h('p', { class: 'muted', role: 'status' });
  const filter = h('select', { 'aria-label': 'Filter by state', onchange: () => { state = filter.value; offset = 0; load(); } },
    STATES.map((value) => h('option', { value }, value ? value[0].toUpperCase() + value.slice(1) : 'All states')));
  const prev = h('button', { type: 'button', onclick: () => { offset = Math.max(0, offset - PAGE); load(); } }, 'Newer');
  const next = h('button', { type: 'button', onclick: () => { offset += PAGE; load(); } }, 'Older');

  async function load() {
    clearTimeout(timer);
    const mine = ++token;
    let delay = IDLE_MS;
    try {
      const page = await api.listJobs({ offset, limit: PAGE, state });
      if (stopped || mine !== token) return;
      if (page.items.length === 0 && offset > 0) { offset = Math.max(0, offset - PAGE); return load(); }
      delay = page.items.some((job) => job.state === 'queued' || job.state === 'running') ? ACTIVE_MS : IDLE_MS;
      replace(body, page.items.map((job) => h('tr', {},
        h('td', {}, h('a', { href: jobHref(job.id) }, job.inputs.map((name) => displayName(name, job.has_upload)).join(', ') || job.id.slice(0, 12))),
        h('td', {}, job.state), h('td', {}, job.current_stage),
        h('td', {}, formatDateTime(job.submitted_at)))));
      replace(note, page.total === 0 ? (state ? 'No jobs in this state.' : 'No jobs yet. Start one from New job.') : `${offset + 1}–${offset + page.items.length} of ${page.total}`);
      prev.disabled = offset === 0;
      next.disabled = offset + PAGE >= page.total;
    } catch (error) {
      if (stopped || mine !== token) return;
      replace(note, error.message);
    }
    if (!stopped && mine === token) timer = setTimeout(load, delay);
  }

  replace(container, h('h1', {}, 'Jobs'),
    h('div', { class: 'row' }, filter, prev, next),
    h('table', {}, h('thead', {}, h('tr', {}, ['Files', 'State', 'Stage', 'Submitted'].map((t) => h('th', {}, t)))), body),
    note);
  load();
  return () => { stopped = true; clearTimeout(timer); };
}
