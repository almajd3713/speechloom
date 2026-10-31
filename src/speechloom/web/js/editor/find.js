// Filtering lines. No DOM access.

const fold = (text) => text.toLocaleLowerCase();

export function matches(row, query) {
  const needle = fold(query.trim());
  if (!needle) return true;
  return [row.text, row.translation ?? '', row.speaker].some((field) => fold(field).includes(needle));
}
