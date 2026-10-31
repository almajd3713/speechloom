// Builds the lines of the editor from a transcript, its saved edits and a translation.
// No DOM access.

import { collapse } from './text.js';

export const speakerLabel = (speaker) => (speaker === null || speaker === undefined ? '' : `Speaker ${speaker}`);

// Edits count only when made against this exact transcript (same rule as the service's merge).
export function currentEdits(edited, transcriptSha) {
  if (!edited || typeof edited !== 'object' || edited.schema_version !== 1) return new Map();
  if (!transcriptSha || edited.source_transcript_sha256 !== transcriptSha) return new Map();
  if (!Array.isArray(edited.edited_indices) || !Array.isArray(edited.segments)) return new Map();
  const wanted = new Set(edited.edited_indices.filter((index) => Number.isInteger(index)));
  const texts = new Map();
  for (const item of edited.segments) {
    if (item && wanted.has(item.index) && typeof item.text === 'string') texts.set(item.index, item.text);
  }
  return texts;
}

// Returns { rows, note }. The translation shows only when it lines up with the transcript.
export function buildRows({ segments, edited = null, transcriptSha = null, translation = null }) {
  const edits = currentEdits(edited, transcriptSha);
  let note = null;
  let translated = null;
  if (translation) {
    if (Array.isArray(translation.segments) && translation.segments.length === segments.length) {
      translated = translation.segments;
    } else {
      const count = Array.isArray(translation.segments) ? translation.segments.length : 0;
      note = `The translation has ${count} lines and the transcript has ${segments.length}, so translations can’t be shown beside each line. Download the translation to read it.`;
    }
  }
  const rows = segments.map((segment, index) => {
    const original = collapse(segment.text);
    const text = edits.has(index) ? edits.get(index) : original;
    return {
      index,
      start: segment.start,
      end: segment.end,
      speaker: speakerLabel(segment.speaker),
      original,
      text,
      edited: text !== original,
      translation: translated ? translated[index].text : null,
    };
  });
  return { rows, note };
}
