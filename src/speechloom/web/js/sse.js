// Incremental parser for text/event-stream. No DOM access, so Node can test it.

export function createSseParser(onEvent) {
  let buffer = '';
  let id = '';
  let event = '';
  let data = [];
  let hasData = false;
  let skipLf = false;

  function dispatch() {
    if (hasData) {
      onEvent({ id: id || null, event: event || 'message', data: data.join('\n') });
    }
    event = '';
    data = [];
    hasData = false;
  }

  function line(text) {
    if (text === '') {
      dispatch();
      return;
    }
    if (text.startsWith(':')) return;
    const colon = text.indexOf(':');
    const field = colon === -1 ? text : text.slice(0, colon);
    let value = colon === -1 ? '' : text.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'data') {
      data.push(value);
      hasData = true;
    } else if (field === 'event') {
      event = value;
    } else if (field === 'id' && !value.includes('\0')) {
      id = value;
    }
  }

  return {
    feed(chunk) {
      let text = buffer + chunk;
      if (skipLf && text.startsWith('\n')) text = text.slice(1);
      skipLf = false;
      let start = 0;
      for (let i = 0; i < text.length; i += 1) {
        const ch = text[i];
        if (ch !== '\n' && ch !== '\r') continue;
        line(text.slice(start, i));
        if (ch === '\r') {
          if (i + 1 < text.length) {
            if (text[i + 1] === '\n') i += 1;
          } else {
            skipLf = true; // a "\n" may open the next chunk
          }
        }
        start = i + 1;
      }
      buffer = text.slice(start);
    },
    // Called when the stream ends: an unfinished line or event is dropped, as browsers do.
    end() {
      buffer = '';
      skipLf = false;
      event = '';
      data = [];
      hasData = false;
    },
    get lastEventId() {
      return id || null;
    },
  };
}
