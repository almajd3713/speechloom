// What the Setup screen says about the installation. No DOM access.

export const FEATURES = [
  { name: 'diarization', label: 'Speaker labels', detail: 'about 150 MB' },
  { name: 'translation', label: 'Translation', detail: 'about 4.4 GB, converted on this computer' },
];
export const BACKENDS = ['auto', 'cpu', 'cuda', 'vulkan'];

// status: the /v1/setup status (or null); capabilities: /v1/capabilities (or null).
// A hand-written configuration can make transcription work while setup's own record says
// "not installed"; say so instead of contradicting the banner.
export function summarize(status, capabilities) {
  const ready = capabilities?.features?.transcription === true;
  if (!status) return { headline: 'Checking the installation…', notes: [], tone: 'muted' };
  const notes = [];
  if (!status.installed) {
    if (ready) {
      notes.push('Speechloom’s own installation record is missing, but your configuration already points at a working engine and models, so transcription is ready. Running setup would install managed copies alongside them.');
      return { headline: 'Using your own configuration', notes, tone: 'ok' };
    }
    return { headline: 'Not installed yet', notes: ['Choose a backend below and install the engine and speech model.'], tone: 'warning' };
  }
  for (const issue of status.issues ?? []) notes.push(issue);
  if (!status.ready) return { headline: 'Installed, but needs repair', notes, tone: 'warning' };
  return { headline: 'Installed and ready', notes, tone: 'ok' };
}

export function logLine(event) {
  const time = new Date(event.timestamp);
  const stamp = Number.isNaN(time.getTime()) ? '' : `${time.toLocaleTimeString()}  `;
  return `${stamp}${event.message}`;
}

export function checkTone(status) {
  return status === 'ok' ? 'ok' : status === 'warn' ? 'warning' : 'error';
}
