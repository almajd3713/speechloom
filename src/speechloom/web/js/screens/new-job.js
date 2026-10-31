import { jobHref } from '../router.js';
import { h, replace } from '../dom.js';
import { formatBytes } from '../format.js';
import {
  ACCEPT,
  FORMATS,
  addFiles,
  buildOptions,
  buildPathBody,
  parsePaths,
  totalSize,
  uploadLimitMessage,
} from '../options.js';

const languageName = (code) => {
  try {
    return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) ?? code;
  } catch {
    return code;
  }
};

export function mount(container, { api, capabilities }) {
  let files = [];
  let busy = false;
  let abort = null;

  const message = h('p', { class: 'form-message', role: 'alert' });
  const showMessage = (text) => replace(message, text ?? '');

  const list = h('ul', { class: 'file-list' });
  const summary = h('p', { class: 'muted' });
  const progress = h('progress', { max: 100, value: 0, hidden: true });
  const picker = h('input', {
    type: 'file', multiple: true, accept: ACCEPT, class: 'visually-hidden', id: 'file-input',
    onchange: () => { add([...picker.files]); picker.value = ''; },
  });
  const drop = h('label', { class: 'dropzone', for: 'file-input', tabindex: 0 },
    h('strong', {}, 'Drop audio or video files here'), h('span', { class: 'muted' }, 'or click to choose files'));
  drop.addEventListener('dragover', (event) => { event.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (event) => {
    event.preventDefault();
    drop.classList.remove('over');
    add([...event.dataTransfer.files]);
  });
  drop.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); picker.click(); } });

  const paths = h('textarea', { rows: 3, placeholder: '/home/me/Videos/talk.mp4', 'aria-label': 'Local paths, one per line' });
  const recursive = h('input', { type: 'checkbox' });
  const pathsBox = h('div', { class: 'card', hidden: true },
    h('h2', {}, 'Local paths'),
    h('p', { class: 'muted' }, 'One file or folder per line. The server reads them directly, so nothing is uploaded.'),
    paths, h('label', {}, recursive, ' Include subfolders'));

  const formatBoxes = FORMATS.map((format) => h('input', { type: 'checkbox', value: format, checked: true, id: `fmt-${format}` }));
  const diarize = h('input', { type: 'checkbox', id: 'diarize' });
  const diarizeHint = h('span', { class: 'muted' });
  const translate = h('input', { type: 'checkbox', id: 'translate' });
  const translateHint = h('span', { class: 'muted' });
  const languageRow = h('div', { class: 'row' });
  const source = h('select', { 'aria-label': 'Spoken language' });
  const target = h('select', { 'aria-label': 'Translate to' });
  const adv = {
    audioStream: h('input', { type: 'number', min: 0, step: 1, 'aria-label': 'Audio stream' }),
    workers: h('input', { type: 'number', min: 1, max: 8, step: 1, 'aria-label': 'Workers' }),
    resume: h('input', { type: 'checkbox', checked: true }),
    force: h('input', { type: 'checkbox' }),
    keepAudio: h('input', { type: 'checkbox' }),
    failFast: h('input', { type: 'checkbox' }),
  };
  const submit = h('button', { class: 'primary', type: 'submit' }, 'Transcribe');

  function fillLanguages(caps) {
    const options = (codes) => codes.map((code) => h('option', { value: code }, `${languageName(code)} (${code})`));
    replace(source, h('option', { value: '' }, 'Choose…'), options(caps?.asr_languages ?? []));
    replace(target, h('option', { value: '' }, 'Choose…'), options(caps?.translation_languages ?? []));
  }

  function refresh() {
    const caps = capabilities.get();
    fillLanguages(caps);
    const features = caps?.features ?? {};
    const canDiarize = features.diarization === true;
    const canTranslate = features.translation === true;
    diarize.disabled = !canDiarize;
    if (!canDiarize) diarize.checked = false;
    replace(diarizeHint, canDiarize ? '' : caps ? ' Install the diarization feature in Setup to use this.' : '');
    translate.disabled = !canTranslate;
    if (!canTranslate) translate.checked = false;
    replace(translateHint, canTranslate ? '' : caps ? ' Install the translation feature in Setup to use this.' : '');
    source.disabled = target.disabled = !translate.checked;
    languageRow.hidden = !translate.checked;
    pathsBox.hidden = caps?.local_path_input !== true;
    const ready = features.transcription !== false;
    submit.disabled = busy || !ready;
    submit.title = ready ? '' : 'Transcription isn’t ready. Open Setup first.';
  }

  function renderFiles() {
    replace(list, files.map((file, index) => h('li', {},
      h('span', {}, file.name), ' ', h('span', { class: 'muted' }, formatBytes(file.size)), ' ',
      h('button', { type: 'button', 'aria-label': `Remove ${file.name}`, onclick: () => { files = files.filter((_, i) => i !== index); renderFiles(); } }, 'Remove'))));
    replace(summary, files.length ? `${files.length} ${files.length === 1 ? 'file' : 'files'}, ${formatBytes(totalSize(files))}` : '');
  }

  function add(incoming) {
    const result = addFiles(files, incoming);
    files = result.files;
    renderFiles();
    showMessage(result.ignored.length ? `Skipped files that aren’t audio or video: ${result.ignored.join(', ')}` : '');
  }

  async function onSubmit(event) {
    event.preventDefault();
    if (busy) return;
    const caps = capabilities.get();
    const pathList = pathsBox.hidden ? [] : parsePaths(paths.value);
    if (files.length === 0 && pathList.length === 0) return showMessage('Add at least one file.');
    if (files.length > 0 && pathList.length > 0) return showMessage('Use either uploaded files or local paths, not both.');
    const { options, error } = buildOptions({
      formats: formatBoxes.filter((box) => box.checked).map((box) => box.value),
      diarize: diarize.checked,
      translate: translate.checked,
      sourceLanguage: source.value,
      targetLanguage: target.value,
      advanced: {
        audioStream: adv.audioStream.value, workers: adv.workers.value, resume: adv.resume.checked,
        force: adv.force.checked, keepAudio: adv.keepAudio.checked, failFast: adv.failFast.checked,
      },
    });
    if (error) return showMessage(error);
    const tooBig = uploadLimitMessage(files, caps?.max_upload_bytes, caps?.local_path_input === true);
    if (tooBig) return showMessage(tooBig);

    busy = true;
    abort = new AbortController();
    refresh();
    showMessage('');
    try {
      let job;
      if (pathList.length > 0) {
        job = await api.createJob(buildPathBody(pathList, options, recursive.checked));
      } else {
        progress.hidden = false;
        progress.value = 0;
        job = await api.uploadJob(files, options, {
          signal: abort.signal,
          onProgress: (loaded, total) => { progress.value = total ? (loaded / total) * 100 : 0; },
        });
      }
      location.hash = jobHref(job.id);
    } catch (error) {
      if (error.name !== 'AbortError') showMessage(error.message);
    } finally {
      busy = false;
      progress.hidden = true;
      refresh();
    }
  }

  const row = (...children) => h('div', { class: 'row' }, ...children);
  languageRow.append(h('label', {}, 'Spoken language ', source), h('label', {}, 'Translate to ', target));
  const form = h('form', { onsubmit: onSubmit },
    h('div', { class: 'card' }, drop, picker, summary, list, progress),
    pathsBox,
    h('div', { class: 'card' },
      h('h2', {}, 'Output formats'),
      row(FORMATS.map((format, i) => h('label', {}, formatBoxes[i], ` ${format.toUpperCase()}`))),
      h('h2', {}, 'Options'),
      row(h('label', {}, diarize, ' Label speakers'), diarizeHint),
      row(h('label', {}, translate, ' Translate'), translateHint),
      languageRow,
      h('details', {}, h('summary', {}, 'Advanced'),
        row(h('label', {}, 'Audio stream ', adv.audioStream), h('label', {}, 'Workers ', adv.workers)),
        row(h('label', {}, adv.resume, ' Resume earlier work')),
        row(h('label', {}, adv.force, ' Redo finished work (replaces edits)')),
        row(h('label', {}, adv.keepAudio, ' Keep the converted audio')),
        row(h('label', {}, adv.failFast, ' Stop at the first failure')))),
    message,
    submit);
  translate.addEventListener('change', refresh);

  replace(container, h('h1', {}, 'New job'), form);
  renderFiles();
  refresh();
  const off = capabilities.subscribe(refresh);
  return () => { off(); abort?.abort(); };
}
