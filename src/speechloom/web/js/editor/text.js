// Text rules for edited lines. They mirror the service: Python's str.split() collapsing, a
// 1 to 1000 character limit counted in code points. No DOM access.

// Python's str.isspace(): not JavaScript's \s, which has U+FEFF but lacks U+001C-U+001F and U+0085.
// Code point ranges are written as numbers so no invisible characters sit in this file.
const PY_SPACE_RANGES = [
  [0x09, 0x0d], [0x1c, 0x20], [0x85, 0x85], [0xa0, 0xa0], [0x1680, 0x1680],
  [0x2000, 0x200a], [0x2028, 0x2029], [0x202f, 0x202f], [0x205f, 0x205f], [0x3000, 0x3000],
];
const isPySpace = (ch) => {
  const code = ch.codePointAt(0);
  return PY_SPACE_RANGES.some(([low, high]) => code >= low && code <= high);
};

export const MAX_LINE_CHARS = 1000;

export function collapse(text) {
  const words = [];
  let word = '';
  for (const ch of text) {
    if (isPySpace(ch)) {
      if (word) words.push(word);
      word = '';
    } else {
      word += ch;
    }
  }
  if (word) words.push(word);
  return words.join(' ');
}

export const codePointLength = (text) => [...text].length;

// What to do with the text a user left in a line:
//   { kind: 'restore' }          it equals the original: send null
//   { kind: 'set', text }        send this text
//   { kind: 'invalid', error }   do not save; show the error
export function judgeEdit(typed, original) {
  const text = collapse(typed);
  if (text === original) return { kind: 'restore', text };
  if (text.length === 0) return { kind: 'invalid', error: 'A line can’t be empty. Revert it to restore the original.' };
  if (codePointLength(text) > MAX_LINE_CHARS) {
    return { kind: 'invalid', error: `A line can have at most ${MAX_LINE_CHARS} characters (this one has ${codePointLength(text)}).` };
  }
  return { kind: 'set', text };
}
