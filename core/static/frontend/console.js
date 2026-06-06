/* Baselith-Core Console — dependency-free client.
 * Talks to the existing API surface (/health, /status, /chat/stream, /chat).
 * No build step, no external scripts: served same-origin so it satisfies the
 * strict runtime CSP (script-src 'self'). */

(() => {
  'use strict';

  const KEY_STORAGE = 'baselith.apiKey';
  const $ = (id) => document.getElementById(id);

  const el = {
    health: $('health-badge'),
    messages: $('messages'),
    form: $('chat-form'),
    input: $('chat-input'),
    send: $('send-btn'),
    newConvo: $('new-convo'),
    apiKey: $('api-key'),
    saveKey: $('save-key'),
    clearKey: $('clear-key'),
    keyStatus: $('key-status'),
    sysInfo: $('system-info'),
    refresh: $('refresh-status'),
  };

  let conversationId = newConversationId();
  let sending = false;

  function newConversationId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'c-' + Date.now().toString(36) + Math.floor(Math.random() * 1e6).toString(36);
  }

  function getKey() {
    return localStorage.getItem(KEY_STORAGE) || '';
  }

  function authHeaders(base) {
    const h = base || {};
    const k = getKey();
    if (k) h['X-API-Key'] = k;
    return h;
  }

  // ---- Messages -----------------------------------------------------------

  function clearEmptyState() {
    const empty = el.messages.querySelector('.empty-state');
    if (empty) empty.remove();
  }

  function addMessage(role, text) {
    clearEmptyState();
    const wrap = document.createElement('div');
    wrap.className = 'msg ' + role;
    const label = document.createElement('div');
    label.className = 'role';
    label.textContent = role === 'user' ? 'You' : role === 'error' ? 'Error' : 'Agent';
    const body = document.createElement('div');
    body.className = 'body';
    body.textContent = text;
    wrap.appendChild(label);
    wrap.appendChild(body);
    el.messages.appendChild(wrap);
    el.messages.scrollTop = el.messages.scrollHeight;
    return { wrap, body };
  }

  function addSources(wrap, sources) {
    if (!Array.isArray(sources) || sources.length === 0) return;
    const box = document.createElement('div');
    box.className = 'sources';
    const names = sources.map((s) => s.title || s.source || s.id || s.url || 'source').slice(0, 6);
    box.textContent = 'Sources: ' + names.join(' · ');
    wrap.appendChild(box);
  }

  // ---- Chat ---------------------------------------------------------------

  async function sendMessage(query) {
    if (sending || !query.trim()) return;
    sending = true;
    el.send.disabled = true;
    addMessage('user', query);

    const { wrap, body } = addMessage('agent', '');
    const cursor = document.createElement('span');
    cursor.className = 'typing';
    body.appendChild(cursor);

    const payload = { query, conversation_id: conversationId };

    try {
      const streamed = await streamChat(payload, body, cursor);
      if (!streamed) {
        // Fall back to the non-streaming endpoint.
        cursor.remove();
        await blockingChat(payload, wrap, body);
      } else {
        cursor.remove();
      }
    } catch (err) {
      cursor.remove();
      wrap.className = 'msg error';
      body.textContent = friendlyError(err);
    } finally {
      sending = false;
      el.send.disabled = false;
      el.messages.scrollTop = el.messages.scrollHeight;
    }
  }

  async function streamChat(payload, body, cursor) {
    const res = await fetch('/chat/stream', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw httpError(res);
    if (!res.body || !res.body.getReader) return false; // no streaming support

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let acc = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      acc += decoder.decode(value, { stream: true });
      body.textContent = acc;
      body.appendChild(cursor);
      el.messages.scrollTop = el.messages.scrollHeight;
    }
    body.textContent = acc;
    return true;
  }

  async function blockingChat(payload, wrap, body) {
    const res = await fetch('/chat', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw httpError(res);
    const data = await res.json();
    body.textContent = data.answer || '(empty response)';
    if (data.conversation_id) conversationId = data.conversation_id;
    addSources(wrap, data.sources);
  }

  function httpError(res) {
    const e = new Error('HTTP ' + res.status);
    e.status = res.status;
    return e;
  }

  function friendlyError(err) {
    if (err && err.status === 401) return 'Unauthorized (401). Set a valid API key in the sidebar.';
    if (err && err.status === 403)
      return 'Forbidden (403). Your key lacks permission for this action.';
    if (err && err.status === 429) return 'Rate limited (429). Slow down and retry.';
    if (err && err.status) return 'Request failed (HTTP ' + err.status + ').';
    return 'Network error: ' + (err && err.message ? err.message : 'request failed') + '.';
  }

  // ---- Health & status ----------------------------------------------------

  async function pollHealth() {
    try {
      const res = await fetch('/health', { headers: authHeaders() });
      const ok = res.ok && (await res.json()).status === 'ok';
      el.health.className = 'badge ' + (ok ? 'badge-ok' : 'badge-down');
      el.health.textContent = ok ? '● online' : '● degraded';
    } catch {
      el.health.className = 'badge badge-down';
      el.health.textContent = '● offline';
    }
  }

  function kvItem(k, v) {
    const item = document.createElement('div');
    item.className = 'item';
    const kk = document.createElement('span');
    kk.className = 'k';
    kk.textContent = k;
    const vv = document.createElement('span');
    vv.className = 'v';
    vv.textContent = v;
    item.appendChild(kk);
    item.appendChild(vv);
    return item;
  }

  function flatten(obj, prefix, out, depth) {
    out = out || [];
    depth = depth || 0;
    for (const key of Object.keys(obj)) {
      const val = obj[key];
      const label = prefix ? prefix + '.' + key : key;
      if (val && typeof val === 'object' && !Array.isArray(val) && depth < 1) {
        flatten(val, label, out, depth + 1);
      } else {
        out.push([label, Array.isArray(val) ? val.length + ' items' : String(val)]);
      }
    }
    return out;
  }

  async function loadStatus() {
    el.sysInfo.textContent = '';
    const loading = document.createElement('div');
    loading.className = 'muted small';
    loading.textContent = 'Loading status…';
    el.sysInfo.appendChild(loading);
    try {
      const res = await fetch('/status', { headers: authHeaders() });
      if (!res.ok) {
        el.sysInfo.textContent = '';
        const m = document.createElement('div');
        m.className = 'muted small';
        m.textContent =
          res.status === 401 || res.status === 403
            ? 'Status needs an admin API key.'
            : 'Status unavailable (HTTP ' + res.status + ').';
        el.sysInfo.appendChild(m);
        return;
      }
      const data = await res.json();
      el.sysInfo.textContent = '';
      const rows = flatten(data, '').slice(0, 14);
      if (rows.length === 0) {
        el.sysInfo.appendChild(kvItem('status', 'ok'));
      }
      for (const [k, v] of rows) el.sysInfo.appendChild(kvItem(k, v));
    } catch (err) {
      el.sysInfo.textContent = '';
      const m = document.createElement('div');
      m.className = 'muted small';
      m.textContent = 'Status error: ' + (err.message || 'failed');
      el.sysInfo.appendChild(m);
    }
  }

  // ---- Key management -----------------------------------------------------

  function reflectKey() {
    const k = getKey();
    el.apiKey.value = k;
    el.keyStatus.textContent = k ? 'Key saved (sent as X-API-Key).' : 'No key set.';
  }

  // ---- Wiring -------------------------------------------------------------

  function autosize() {
    el.input.style.height = 'auto';
    el.input.style.height = Math.min(el.input.scrollHeight, 160) + 'px';
  }

  el.form.addEventListener('submit', (e) => {
    e.preventDefault();
    const q = el.input.value;
    el.input.value = '';
    autosize();
    sendMessage(q);
  });

  el.input.addEventListener('input', autosize);
  el.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      el.form.requestSubmit();
    }
  });

  el.newConvo.addEventListener('click', () => {
    conversationId = newConversationId();
    el.messages.textContent = '';
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    const p = document.createElement('p');
    p.textContent = 'New conversation started.';
    empty.appendChild(p);
    el.messages.appendChild(empty);
  });

  el.saveKey.addEventListener('click', () => {
    const v = el.apiKey.value.trim();
    if (v) localStorage.setItem(KEY_STORAGE, v);
    else localStorage.removeItem(KEY_STORAGE);
    reflectKey();
    pollHealth();
    loadStatus();
  });

  el.clearKey.addEventListener('click', () => {
    localStorage.removeItem(KEY_STORAGE);
    reflectKey();
  });

  el.refresh.addEventListener('click', loadStatus);

  // ---- Init ---------------------------------------------------------------

  reflectKey();
  pollHealth();
  loadStatus();
  setInterval(pollHealth, 15000);
})();
