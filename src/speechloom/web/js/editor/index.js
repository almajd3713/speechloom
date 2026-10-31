// The transcript editor: lines beside a player, edited in place and saved automatically.

import { h, replace } from '../dom.js';
import { formatDuration } from '../format.js';
import { matches } from './find.js';
import { currentRow } from './playback.js';
import { createSaveQueue } from './queue.js';
import { buildRows } from './rows.js';
import { judgeEdit } from './text.js';

const FIND_DELAY_MS = 150;

function plainTextMode() {
  const probe = document.createElement('div');
  probe.contentEditable = 'plaintext-only';
  return probe.contentEditable === 'plaintext-only' ? 'plaintext-only' : 'true';
}

async function loadJson(api, jobId, name, signal) {
  return JSON.parse(await api.fetchArtifactText(jobId, name, signal));
}

export function mount(container, { api, jobId, result, resultCount, onSaved }) {
  const controller = new AbortController();
  let queue = null;
  let media = null;
  let rows = [];
  let views = [];
  let current = -1;
  let cleanup = [];
  let gone = false;

  replace(container, h('h2', {}, 'Transcript'), h('p', { class: 'muted' }, 'Loading the transcript…'));

  async function load() {
    const prefix = resultCount > 1 ? `${result}.` : '';
    const { items } = await api.listArtifacts(jobId);
    const byName = new Map(items.map((item) => [item.name, item]));
    const transcriptItem = byName.get(`${prefix}json`);
    if (!transcriptItem) throw new Error('This job has no transcript to edit.');
    const translationItem = items.find((item) => new RegExp(`^${prefix.replace('.', '\\.')}translation\\.[^.]+\\.json$`).test(item.name));
    const [transcript, edited, translation] = await Promise.all([
      loadJson(api, jobId, transcriptItem.name, controller.signal),
      byName.has(`${prefix}edited.json`) ? loadJson(api, jobId, `${prefix}edited.json`, controller.signal).catch(() => null) : null,
      translationItem ? loadJson(api, jobId, translationItem.name, controller.signal).catch(() => null) : null,
    ]);
    const built = buildRows({
      segments: transcript.segments ?? [],
      edited,
      transcriptSha: transcriptItem.sha256,
      translation,
    });
    rows = built.rows.map((row) => ({ ...row, error: null }));
    return built.note;
  }

  function build(note) {
    const mode = plainTextMode();
    const status = h('span', { class: 'save-status', role: 'status' }, 'All changes saved');
    const retry = h('button', { type: 'button', hidden: true, onclick: () => queue.retry() }, 'Retry');
    const revertAll = h('button', { type: 'button', onclick: () => rows.forEach((row) => revert(row)) }, 'Revert all');
    const count = h('span', { class: 'muted' });
    let findTimer = null;
    const find = h('input', {
      type: 'search', placeholder: 'Find in transcript', 'aria-label': 'Find in transcript',
      oninput: () => { clearTimeout(findTimer); findTimer = setTimeout(applyFind, FIND_DELAY_MS); },
    });
    cleanup.push(() => clearTimeout(findTimer));
    const lines = h('div', { class: 'lines' });
    const player = h('div', { class: 'player', hidden: true });

    views = rows.map((row) => {
      const text = h('div', { class: 'text', contentEditable: mode, spellcheck: true, role: 'textbox', 'aria-label': `Line ${row.index + 1}` }, row.text);
      const error = h('div', { class: 'line-error', role: 'alert', hidden: true });
      const revertButton = h('button', { type: 'button', class: 'revert', hidden: !row.edited, 'data-act': 'revert' }, 'Revert');
      const el = h('div', { class: 'line', 'data-index': row.index },
        h('button', { type: 'button', class: 'time', 'data-act': 'seek' }, formatDuration(row.start)),
        h('div', { class: 'line-body' },
          row.speaker ? h('span', { class: 'speaker' }, row.speaker) : null,
          text,
          row.translation ? h('div', { class: 'translation' }, row.translation) : null,
          error),
        revertButton);
      el.classList.toggle('edited', row.edited);
      return { el, text, error, revertButton };
    });
    lines.append(...views.map((view) => view.el));

    function paintRow(row) {
      const view = views[row.index];
      view.el.classList.toggle('edited', row.edited);
      view.el.classList.toggle('invalid', Boolean(row.error));
      view.revertButton.hidden = !(row.edited || row.error);
      view.error.hidden = !row.error;
      view.error.textContent = row.error ?? '';
    }

    function revert(row) {
      if (!row.edited && !row.error) return;
      row.text = row.original;
      row.edited = false;
      row.error = null;
      views[row.index].text.textContent = row.original;
      paintRow(row);
      queue.set(row.index, null);
      updateRevertAll();
    }

    const updateRevertAll = () => { revertAll.disabled = !rows.some((row) => row.edited || row.error); };

    function applyFind() {
      const query = find.value;
      let shown = 0;
      rows.forEach((row, index) => {
        const visible = matches(row, query);
        views[index].el.hidden = !visible;
        if (visible) shown += 1;
      });
      replace(count, query.trim() ? `${shown} of ${rows.length} lines` : `${rows.length} lines`);
    }

    queue = createSaveQueue({
      send: async (changes) => {
        await api.saveEdits(jobId, result, changes);
        onSaved?.();
      },
      onChange: ({ state, error }) => {
        const invalid = rows.filter((row) => row.error).length;
        retry.hidden = state !== 'failed';
        status.classList.toggle('save-failed', state === 'failed');
        replace(status, state === 'failed' ? `Couldn’t save: ${error}`
          : state === 'saving' ? 'Saving…'
            : state === 'pending' ? 'Unsaved changes'
              : invalid ? `${invalid} ${invalid === 1 ? 'line has' : 'lines have'} a problem and aren’t saved`
                : 'All changes saved');
      },
    });

    lines.addEventListener('input', (event) => {
      const el = event.target.closest('.line');
      if (!el) return;
      const row = rows[Number(el.dataset.index)];
      const verdict = judgeEdit(views[row.index].text.textContent, row.original);
      if (verdict.kind === 'invalid') {
        row.error = verdict.error;
        row.edited = true;
        queue.drop(row.index);
      } else {
        row.error = null;
        row.text = verdict.text;
        row.edited = verdict.kind === 'set';
        queue.set(row.index, verdict.kind === 'set' ? verdict.text : null);
      }
      paintRow(row);
      updateRevertAll();
    });
    lines.addEventListener('focusout', (event) => {
      const el = event.target.closest?.('.line');
      if (!el) return;
      const row = rows[Number(el.dataset.index)];
      if (!row.error) views[row.index].text.textContent = row.text;
    });
    lines.addEventListener('beforeinput', (event) => {
      if (event.inputType === 'insertParagraph' || event.inputType === 'insertLineBreak') event.preventDefault();
    });
    lines.addEventListener('paste', (event) => {
      if (mode === 'plaintext-only') return;
      event.preventDefault();
      const text = (event.clipboardData?.getData('text/plain') ?? '').replace(/\s+/g, ' ');
      document.execCommand('insertText', false, text);
    });
    lines.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      const next = event.target.closest('.line')?.nextElementSibling?.querySelector('.text');
      next?.focus();
    });
    lines.addEventListener('click', (event) => {
      const el = event.target.closest('.line');
      if (!el) return;
      const row = rows[Number(el.dataset.index)];
      if (event.target.closest('[data-act="revert"]')) { revert(row); return; }
      if (media && Number.isFinite(row.start)) media.currentTime = row.start;
    });

    const beforeUnload = (event) => {
      if (queue.hasUnsaved() || rows.some((row) => row.error)) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', beforeUnload);
    cleanup.push(() => window.removeEventListener('beforeunload', beforeUnload));

    replace(container,
      h('h2', {}, 'Transcript'),
      note ? h('p', { class: 'muted' }, note) : null,
      h('div', { class: 'editor-bar' }, find, count, status, retry, revertAll),
      h('div', { class: 'editor' }, player, lines));
    applyFind();
    updateRevertAll();
    setupPlayer(player);
  }

  async function setupPlayer(box) {
    let type;
    try { type = await api.mediaType(jobId, result, controller.signal); } catch { return; }
    if (gone) return;
    const unavailable = (text) => { box.hidden = false; box.classList.add('no-media'); replace(box, h('p', { class: 'muted' }, text)); media = null; };
    if (!type) return unavailable('No media is stored for this job, so there’s no player. Editing and downloads still work.');
    const element = h(type.startsWith('video/') ? 'video' : 'audio', { controls: true, preload: 'metadata', src: api.mediaUrl(jobId, result) });
    element.addEventListener('error', () => unavailable('This browser can’t play this media. Editing and downloads still work.'));
    const sync = () => {
      const index = currentRow(rows, element.currentTime);
      if (index === current) return;
      if (current >= 0) views[current]?.el.classList.remove('current');
      current = index;
      if (index >= 0) {
        const el = views[index].el;
        el.classList.add('current');
        if (!element.paused && !el.contains(document.activeElement)) el.scrollIntoView({ block: 'nearest' });
      }
    };
    for (const name of ['timeupdate', 'seeked', 'play']) element.addEventListener(name, sync);
    media = element;
    box.hidden = false;
    replace(box, element);
    cleanup.push(() => element.pause());
  }

  load().then(build, (error) => {
    if (!gone) replace(container, h('h2', {}, 'Transcript'), h('p', { class: 'error-text' }, error.message));
  });

  return () => {
    gone = true;
    controller.abort();
    queue?.flush();
    for (const fn of cleanup) fn();
    cleanup = [];
  };
}
