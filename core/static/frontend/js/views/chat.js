/* Chat view — query the agent with streaming + non-streaming fallback. */

import { h, clear } from '../ui.js';
import { request, streamChat, friendlyError } from '../api.js';

function newConversationId() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return 'c-' + Date.now().toString(36) + Math.floor(Math.random() * 1e6).toString(36);
}

export function mount(root) {
  let conversationId = newConversationId();
  let sending = false;

  const messages = h('div', { class: 'messages' }, [
    h('div', { class: 'empty-state' }, [
      h('p', { text: 'Ask the agent something to get started.' }),
    ]),
  ]);
  const input = h('textarea', {
    class: 'chat-input',
    rows: '1',
    placeholder: 'Type a message…  (Enter to send, Shift+Enter for newline)',
    autocomplete: 'off',
  });
  const sendBtn = h('button', { type: 'submit', class: 'btn-primary', text: 'Send' });

  function addMessage(role, text) {
    const empty = messages.querySelector('.empty-state');
    if (empty) empty.remove();
    const body = h('div', { class: 'body', text });
    const wrap = h('div', { class: 'msg ' + role }, [
      h('div', {
        class: 'role',
        text: role === 'user' ? 'You' : role === 'error' ? 'Error' : 'Agent',
      }),
      body,
    ]);
    messages.appendChild(wrap);
    messages.scrollTop = messages.scrollHeight;
    return { wrap, body };
  }

  function addSources(wrap, sources) {
    if (!Array.isArray(sources) || sources.length === 0) return;
    const names = sources.map((s) => s.title || s.source || s.id || s.url || 'source').slice(0, 6);
    wrap.appendChild(h('div', { class: 'sources', text: 'Sources: ' + names.join(' · ') }));
  }

  async function send(query) {
    if (sending || !query.trim()) return;
    sending = true;
    sendBtn.disabled = true;
    addMessage('user', query);
    const { wrap, body } = addMessage('agent', '');
    const payload = { query, conversation_id: conversationId };
    try {
      let acc = '';
      const streamed = await streamChat(payload, (chunk) => {
        acc += chunk;
        body.textContent = acc;
        messages.scrollTop = messages.scrollHeight;
      });
      if (!streamed) {
        const data = await request('POST', '/chat', { body: payload });
        body.textContent = (data && data.answer) || '(empty response)';
        if (data && data.conversation_id) conversationId = data.conversation_id;
        addSources(wrap, data && data.sources);
      }
    } catch (err) {
      wrap.className = 'msg error';
      body.textContent = friendlyError(err);
    } finally {
      sending = false;
      sendBtn.disabled = false;
      messages.scrollTop = messages.scrollHeight;
    }
  }

  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  }

  const form = h('form', { class: 'composer' }, [input, sendBtn]);
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const q = input.value;
    input.value = '';
    autosize();
    send(q);
  });
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  const newBtn = h('button', { class: 'btn-ghost', text: 'New' });
  newBtn.addEventListener('click', () => {
    conversationId = newConversationId();
    clear(messages).appendChild(
      h('div', { class: 'empty-state' }, [h('p', { text: 'New conversation started.' })])
    );
  });

  clear(root).appendChild(
    h('section', { class: 'panel' }, [
      h('div', { class: 'panel-head' }, [h('h2', { text: 'Chat' }), newBtn]),
      messages,
      form,
    ])
  );
}
