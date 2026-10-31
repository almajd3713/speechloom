import { h, replace } from '../dom.js';

export function mount(container) {
  replace(container, h('h1', {}, 'New job'), h('p', { class: 'muted' }, 'This screen is not built yet.'));
  return null;
}
