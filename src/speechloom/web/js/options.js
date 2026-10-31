// Job form rules shared by the New job screen and tests. No DOM access.

import { formatBytes } from './format.js';

export const FORMATS = ['txt', 'srt', 'vtt', 'json'];
export const MEDIA_EXTENSIONS = [
  '.aac', '.avi', '.flac', '.m4a', '.mka', '.mkv', '.mov', '.mp3',
  '.mp4', '.mpeg', '.mpg', '.oga', '.ogg', '.opus', '.wav', '.webm',
];
export const ACCEPT = MEDIA_EXTENSIONS.join(',');

export const isMedia = (name) => MEDIA_EXTENSIONS.some((ext) => name.toLowerCase().endsWith(ext));

const fileKey = (file) => `${file.name}\0${file.size}\0${file.lastModified ?? 0}`;

// Adds files that are media and not already listed. Returns the new list and what was left out.
export function addFiles(current, incoming) {
  const seen = new Set(current.map(fileKey));
  const list = [...current];
  const ignored = [];
  for (const file of incoming) {
    if (!isMedia(file.name)) {
      ignored.push(file.name);
      continue;
    }
    const key = fileKey(file);
    if (seen.has(key)) continue;
    seen.add(key);
    list.push(file);
  }
  return { files: list, ignored };
}

export function totalSize(files) {
  return files.reduce((sum, file) => sum + file.size, 0);
}

// null when the files fit; otherwise the message to show instead of sending anything.
export function uploadLimitMessage(files, limitBytes, localPaths) {
  const total = totalSize(files);
  if (!limitBytes || total <= limitBytes) return null;
  const advice = localPaths
    ? 'Restart with a larger --max-upload-mb, or enter local paths instead.'
    : 'Restart with a larger --max-upload-mb.';
  return `The selected files total ${formatBytes(total)}, but the server accepts at most ${formatBytes(limitBytes)} per upload. ${advice}`;
}

// Returns { options, error }. Formats are always sent explicitly.
export function buildOptions(form) {
  const formats = FORMATS.filter((format) => form.formats.includes(format));
  if (formats.length === 0) return { error: 'Choose at least one output format.' };
  const options = { formats };
  if (form.diarize) options.diarize = true;
  if (form.translate) {
    if (!form.sourceLanguage || !form.targetLanguage) {
      return { error: 'Choose both the spoken language and the language to translate to.' };
    }
    if (form.sourceLanguage === form.targetLanguage) {
      return { error: 'The spoken language and the translation language must be different.' };
    }
    options.source_language = form.sourceLanguage;
    options.translate_to = form.targetLanguage;
  }
  const advanced = form.advanced ?? {};
  if (advanced.audioStream !== '' && advanced.audioStream != null) {
    const stream = Number(advanced.audioStream);
    if (!Number.isInteger(stream) || stream < 0) return { error: 'The audio stream must be a whole number, 0 or more.' };
    options.audio_stream = stream;
  }
  if (advanced.workers !== '' && advanced.workers != null) {
    const workers = Number(advanced.workers);
    if (!Number.isInteger(workers) || workers < 1 || workers > 8) return { error: 'Workers must be a whole number from 1 to 8.' };
    options.workers = workers;
  }
  for (const [key, field] of [['force', 'force'], ['keep_audio', 'keepAudio'], ['fail_fast', 'failFast']]) {
    if (advanced[field]) options[key] = true;
  }
  if (advanced.resume === false) options.resume = false;
  return { options };
}

// Local-path jobs also allow folders and recursion.
export function buildPathBody(paths, options, recursive) {
  const body = { inputs: paths, ...options };
  if (recursive) body.recursive = true;
  return body;
}

export function parsePaths(text) {
  return text.split('\n').map((line) => line.trim()).filter(Boolean);
}
