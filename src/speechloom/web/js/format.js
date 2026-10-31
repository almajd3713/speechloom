// Number and time formatting shared by the screens. No DOM access.

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return '–';
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${UNITS[unit]}`;
}

// 75 -> "1:15", 3725 -> "1:02:05"
export function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '–';
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (value) => String(value).padStart(2, '0');
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
}

export function formatPercent(fraction) {
  if (!Number.isFinite(fraction)) return '–';
  return `${Math.round(clamp(fraction, 0, 1) * 100)} %`;
}

export function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

export function formatDateTime(iso, locale) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '–';
  return date.toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' });
}

export function pluralize(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}
