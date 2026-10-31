// Groups a job's artifacts by file. Names in multi-file jobs start with the file's position ("2.srt").
// No DOM access.

export function groupArtifacts(items, resultCount) {
  if (resultCount <= 1) return [{ result: 1, items: items.map((item) => ({ ...item, label: item.name })) }];
  const groups = Array.from({ length: resultCount }, (_, index) => ({ result: index + 1, items: [] }));
  for (const item of items) {
    const match = /^(\d+)\.(.+)$/.exec(item.name);
    const position = match ? Number(match[1]) : 0;
    if (match && position >= 1 && position <= resultCount) {
      groups[position - 1].items.push({ ...item, label: match[2] });
    }
  }
  return groups;
}

export function formatLabel(label) {
  const base = label.replace(/^edited\./, '');
  const edited = label.startsWith('edited.');
  const names = { txt: 'Plain text', srt: 'SRT subtitles', vtt: 'WebVTT subtitles', json: 'JSON transcript' };
  const text = names[base] ?? label;
  return edited ? `${text} (edited)` : text;
}
