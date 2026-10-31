// Which line the player is on. No DOM access.

const LINGER_SECONDS = 2;

// Rows are sorted by start. A line stays current through the gap after it, up to a short linger.
export function currentRow(rows, time) {
  if (!Number.isFinite(time) || rows.length === 0 || time < rows[0].start) return -1;
  let low = 0;
  let high = rows.length - 1;
  while (low < high) {
    const mid = (low + high + 1) >> 1;
    if (rows[mid].start <= time) low = mid;
    else high = mid - 1;
  }
  return time <= rows[low].end + LINGER_SECONDS ? low : -1;
}
