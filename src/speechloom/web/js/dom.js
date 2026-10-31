// Builds elements from data. Text always goes in as text nodes: nothing here parses HTML.

export function h(tag, props = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value === false || value == null) continue;
    if (key.startsWith('on') && typeof value === 'function') {
      element.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'class') {
      element.className = value;
    } else if (key in element && key !== 'list' && key !== 'form') {
      element[key] = value;
    } else {
      element.setAttribute(key, value === true ? '' : String(value));
    }
  }
  append(element, children);
  return element;
}

export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function replace(parent, ...children) {
  parent.replaceChildren();
  append(parent, children);
}
