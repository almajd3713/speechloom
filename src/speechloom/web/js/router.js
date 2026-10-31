// Hash routes of the interface. No DOM access.

export const ROUTES = {
  new: '#/',
  jobs: '#/jobs',
  storage: '#/storage',
  setup: '#/setup',
};

export function jobHref(id) {
  return `#/jobs/${encodeURIComponent(id)}`;
}

export function parseRoute(hash) {
  const raw = (hash || '').replace(/^#/, '');
  const [path, query = ''] = raw.split('?', 2);
  const segments = path.split('/').filter((part) => part !== '');
  const params = Object.fromEntries(new URLSearchParams(query));
  if (segments.length === 0) return { name: 'new', params, query: params };
  const [first, second, ...rest] = segments;
  if (rest.length === 0) {
    if (first === 'jobs' && second === undefined) return { name: 'jobs', params: {}, query: params };
    if (first === 'jobs') {
      try {
        return { name: 'job', params: { id: decodeURIComponent(second) }, query: params };
      } catch {
        return { name: 'not-found', params: {}, query: {} };
      }
    }
    if ((first === 'storage' || first === 'setup') && second === undefined) {
      return { name: first, params: {}, query: params };
    }
  }
  return { name: 'not-found', params: {}, query: {} };
}

export function navKey(route) {
  return route.name === 'job' ? 'jobs' : route.name;
}
