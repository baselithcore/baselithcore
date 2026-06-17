/* Console bootstrap — hash routing, navigation, API-key panel, health badge. */

import { h, clear } from './ui.js';
import { getKey, setKey } from './api.js';
import { mount as mountChat } from './views/chat.js';
import { mount as mountOverview } from './views/overview.js';
import { mount as mountWebhooks } from './views/webhooks.js';

const ROUTES = [
  { id: 'chat', label: 'Chat', mount: mountChat },
  { id: 'overview', label: 'Overview', mount: mountOverview },
  { id: 'webhooks', label: 'Webhooks', mount: mountWebhooks },
];

const viewRoot = document.getElementById('view-root');
const navRoot = document.getElementById('nav');
const healthBadge = document.getElementById('health-badge');

function currentRoute() {
  const id = (location.hash || '#chat').replace(/^#/, '');
  return ROUTES.find((r) => r.id === id) || ROUTES[0];
}

function renderNav() {
  clear(navRoot);
  const active = currentRoute().id;
  for (const r of ROUTES) {
    navRoot.appendChild(
      h('a', {
        href: '#' + r.id,
        class: 'nav-link' + (r.id === active ? ' active' : ''),
        text: r.label,
      })
    );
  }
}

function renderRoute() {
  renderNav();
  const route = currentRoute();
  try {
    route.mount(viewRoot);
  } catch (err) {
    clear(viewRoot).appendChild(
      h('div', {
        class: 'notice notice-error',
        text: 'View failed to load: ' + (err.message || err),
      })
    );
  }
}

// --- Connection (API key) panel ------------------------------------------
function renderKeyPanel() {
  const root = document.getElementById('conn-panel');
  const input = h('input', {
    id: 'api-key',
    type: 'password',
    placeholder: 'X-API-Key (optional)',
    autocomplete: 'off',
  });
  input.value = getKey();
  const status = h('p', { class: 'muted small', text: getKey() ? 'Key saved.' : 'No key set.' });

  const save = h('button', { class: 'btn-secondary', text: 'Save' });
  save.addEventListener('click', () => {
    setKey(input.value.trim());
    status.textContent = getKey() ? 'Key saved (sent as X-API-Key).' : 'No key set.';
    pollHealth();
    renderRoute();
  });
  const clearBtn = h('button', { class: 'btn-ghost', text: 'Clear' });
  clearBtn.addEventListener('click', () => {
    setKey('');
    input.value = '';
    status.textContent = 'No key set.';
  });

  clear(root).appendChild(
    h('div', null, [
      h('label', { class: 'field' }, [h('span', { text: 'API key' }), input]),
      h('div', { class: 'row' }, [save, clearBtn]),
      status,
    ])
  );
}

// --- Health badge --------------------------------------------------------
async function pollHealth() {
  try {
    const res = await fetch('/health');
    const ok = res.ok && (await res.json()).status === 'ok';
    healthBadge.className = 'badge ' + (ok ? 'badge-ok' : 'badge-down');
    healthBadge.textContent = ok ? '● online' : '● degraded';
  } catch {
    healthBadge.className = 'badge badge-down';
    healthBadge.textContent = '● offline';
  }
}

window.addEventListener('hashchange', renderRoute);
renderKeyPanel();
renderRoute();
pollHealth();
setInterval(pollHealth, 15000);
