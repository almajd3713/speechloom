import { h, replace } from '../dom.js';

export function mount(container) {
  replace(
    container,
    h('h1', {}, 'Page not found'),
    h('p', {}, h('a', { href: '#/' }, 'Go to New job')),
  );
  return null;
}
