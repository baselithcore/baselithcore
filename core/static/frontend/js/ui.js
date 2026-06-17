/* Tiny DOM helpers — no framework, no inline handlers (CSP-safe). */

/** Create an element: h('div', {class:'x'}, [child, 'text']). */
export function h(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') {
        node.addEventListener(k.slice(2).toLowerCase(), v);
      } else node.setAttribute(k, v);
      // Note: no innerHTML path by design — all text goes through textContent
      // to eliminate any XSS vector under the strict CSP.
    }
  }
  for (const c of [].concat(children || [])) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** Build a table from rows of objects, given an ordered column spec. */
export function table(columns, rows, { empty } = {}) {
  if (!rows || rows.length === 0) {
    return h('div', { class: 'muted small', text: empty || 'Nothing to show.' });
  }
  const thead = h(
    'thead',
    null,
    h(
      'tr',
      null,
      columns.map((c) => h('th', { text: c.label }))
    )
  );
  const tbody = h(
    'tbody',
    null,
    rows.map((row) =>
      h(
        'tr',
        null,
        columns.map((c) => {
          const v = c.render ? c.render(row) : row[c.key];
          return h('td', null, typeof v === 'string' || v == null ? String(v ?? '') : v);
        })
      )
    )
  );
  return h('table', { class: 'tbl' }, [thead, tbody]);
}

/** Transient status line inside a container. */
export function notice(text, kind) {
  return h('div', { class: 'notice notice-' + (kind || 'info'), text });
}

/** A labelled section header with an optional action element. */
export function panelHead(title, action) {
  return h('div', { class: 'panel-head' }, [h('h2', { text: title }), action || false]);
}
