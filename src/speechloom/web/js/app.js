// Application shell: routing, navigation, and the banners every screen shows.

import { createClient } from './api.js';
import { h, replace } from './dom.js';
import { navKey, parseRoute } from './router.js';
import { createStore } from './state.js';
import * as jobScreen from './screens/job.js';
import * as jobsScreen from './screens/jobs.js';
import * as newJobScreen from './screens/new-job.js';
import * as notFoundScreen from './screens/not-found.js';
import * as setupScreen from './screens/setup.js';
import * as storageScreen from './screens/storage.js';

const HEALTH_INTERVAL_MS = 10_000;
const SCREENS = {
  new: newJobScreen,
  jobs: jobsScreen,
  job: jobScreen,
  storage: storageScreen,
  setup: setupScreen,
  'not-found': notFoundScreen,
};

const api = createClient();
const online = createStore(true);
const capabilities = createStore(null);

async function refreshCapabilities() {
  try {
    capabilities.set(await api.capabilities());
  } catch {
    // The offline banner already tells the user; keep what we knew.
  }
}

function renderBanners() {
  const offline = !online.get();
  const caps = capabilities.get();
  const notReady = online.get() && caps !== null && caps.features.transcription === false;
  replace(
    document.getElementById('banners'),
    offline &&
      h('div', { class: 'banner banner-error', role: 'alert' },
        'Lost connection to the Speechloom server. Trying again every 10 seconds.'),
    notReady &&
      h('div', { class: 'banner banner-warning' },
        'Transcription isn’t ready yet. ',
        h('a', { href: '#/setup' }, 'Open Setup'),
        ' to install the engine and models.'),
  );
}

async function checkHealth() {
  try {
    await api.health();
    if (!online.get()) {
      online.set(true);
      await refreshCapabilities();
    }
  } catch {
    online.set(false);
  }
}

let unmount = null;

function show() {
  const route = parseRoute(location.hash);
  for (const link of document.querySelectorAll('nav [data-nav]')) {
    if (link.dataset.nav === navKey(route)) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  if (unmount) unmount();
  const view = document.getElementById('view');
  view.replaceChildren();
  const screen = SCREENS[route.name];
  unmount = screen.mount(view, { api, route, online, capabilities, refreshCapabilities }) ?? null;
  window.scrollTo(0, 0);
}

online.subscribe(renderBanners);
capabilities.subscribe(renderBanners);
window.addEventListener('hashchange', show);
setInterval(checkHealth, HEALTH_INTERVAL_MS);
renderBanners();
refreshCapabilities();
show();
