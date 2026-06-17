/* Webhooks management view — list/create/delete endpoints, inspect & replay
 * deliveries. Backed by the /webhooks API (requires webhooks:read / :write). */

import { h, clear, table, notice } from '../ui.js';
import { request, friendlyError } from '../api.js';

export function mount(root) {
  const listBox = h('div', { class: 'kv-block', text: 'Loading endpoints…' });
  const deliveriesBox = h('div', { class: 'kv-block', text: 'Loading deliveries…' });
  const formMsg = h('div');

  // --- Endpoints ---------------------------------------------------------
  async function loadEndpoints() {
    clear(listBox);
    try {
      const data = await request('GET', '/webhooks');
      const rows = data.endpoints || [];
      listBox.appendChild(
        table(
          [
            { key: 'url', label: 'URL' },
            { key: 'events', label: 'Events', render: (r) => (r.event_types || []).join(', ') },
            { key: 'enabled', label: 'Enabled', render: (r) => (r.enabled ? 'yes' : 'no') },
            {
              key: 'actions',
              label: '',
              render: (r) => {
                const del = h('button', { class: 'btn-ghost danger', text: 'Delete' });
                del.addEventListener('click', () => deleteEndpoint(r.id));
                return del;
              },
            },
          ],
          rows,
          { empty: 'No webhook endpoints registered.' }
        )
      );
    } catch (err) {
      listBox.appendChild(h('div', { class: 'muted small', text: friendlyError(err) }));
    }
  }

  async function deleteEndpoint(id) {
    try {
      await request('DELETE', '/webhooks/' + encodeURIComponent(id));
      loadEndpoints();
    } catch (err) {
      clear(formMsg).appendChild(notice(friendlyError(err), 'error'));
    }
  }

  // --- Create ------------------------------------------------------------
  const urlInput = h('input', {
    type: 'url',
    placeholder: 'https://example.com/hooks',
    class: 'fld',
  });
  const eventsInput = h('input', { type: 'text', placeholder: 'chat.completed, *', class: 'fld' });
  const createBtn = h('button', { class: 'btn-primary', text: 'Register' });

  async function create() {
    clear(formMsg);
    const url = urlInput.value.trim();
    if (!url) {
      formMsg.appendChild(notice('URL is required.', 'error'));
      return;
    }
    const events = eventsInput.value
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    createBtn.disabled = true;
    try {
      const res = await request('POST', '/webhooks', {
        body: { url, event_types: events.length ? events : ['*'] },
      });
      urlInput.value = '';
      eventsInput.value = '';
      // The signing secret is shown once — surface it for the operator to copy.
      formMsg.appendChild(
        notice('Endpoint created. Signing secret (shown once): ' + res.secret, 'success')
      );
      loadEndpoints();
    } catch (err) {
      formMsg.appendChild(notice(friendlyError(err), 'error'));
    } finally {
      createBtn.disabled = false;
    }
  }
  createBtn.addEventListener('click', create);

  // --- Deliveries --------------------------------------------------------
  async function loadDeliveries() {
    clear(deliveriesBox);
    try {
      const data = await request('GET', '/webhooks/deliveries');
      const rows = data.deliveries || [];
      deliveriesBox.appendChild(
        table(
          [
            { key: 'event_type', label: 'Event' },
            { key: 'status', label: 'Status' },
            { key: 'attempts', label: 'Attempts' },
            {
              key: 'last_status_code',
              label: 'Code',
              render: (r) => String(r.last_status_code ?? '—'),
            },
            {
              key: 'actions',
              label: '',
              render: (r) => {
                if (r.status !== 'failed') return '';
                const btn = h('button', { class: 'btn-ghost', text: 'Replay' });
                btn.addEventListener('click', () => replay(r.id));
                return btn;
              },
            },
          ],
          rows,
          { empty: 'No deliveries yet.' }
        )
      );
    } catch (err) {
      deliveriesBox.appendChild(h('div', { class: 'muted small', text: friendlyError(err) }));
    }
  }

  async function replay(id) {
    try {
      await request('POST', '/webhooks/deliveries/' + encodeURIComponent(id) + '/replay');
      loadDeliveries();
    } catch (err) {
      clear(formMsg).appendChild(notice(friendlyError(err), 'error'));
    }
  }

  const refresh = h('button', { class: 'btn-ghost', text: 'Refresh' });
  refresh.addEventListener('click', () => {
    loadEndpoints();
    loadDeliveries();
  });

  clear(root).appendChild(
    h('section', { class: 'panel' }, [
      h('div', { class: 'panel-head' }, [h('h2', { text: 'Webhooks' }), refresh]),
      h('div', { class: 'form-row' }, [urlInput, eventsInput, createBtn]),
      formMsg,
      h('h3', { class: 'subhead', text: 'Endpoints' }),
      listBox,
      h('h3', { class: 'subhead', text: 'Recent deliveries' }),
      deliveriesBox,
    ])
  );
  loadEndpoints();
  loadDeliveries();
}
