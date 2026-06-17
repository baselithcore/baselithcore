/* Overview view — liveness, readiness, and (admin) system status. */

import { h, clear, table } from '../ui.js';
import { request, friendlyError } from '../api.js';

function flatten(obj, prefix, out, depth) {
  out = out || [];
  depth = depth || 0;
  for (const key of Object.keys(obj || {})) {
    const val = obj[key];
    const label = prefix ? prefix + '.' + key : key;
    if (val && typeof val === 'object' && !Array.isArray(val) && depth < 1) {
      flatten(val, label, out, depth + 1);
    } else {
      out.push({ key: label, value: Array.isArray(val) ? val.length + ' items' : String(val) });
    }
  }
  return out;
}

export function mount(root) {
  const readyBox = h('div', { class: 'kv-block', text: 'Loading readiness…' });
  const statusBox = h('div', { class: 'kv-block', text: 'Loading status…' });

  async function loadReadiness() {
    clear(readyBox);
    try {
      const data = await request('GET', '/health/ready');
      const services = data.services || {};
      const rows = Object.keys(services).map((k) => ({
        key: k,
        value: services[k] ? 'up' : 'down',
      }));
      rows.unshift({ key: 'status', value: data.status });
      readyBox.appendChild(
        table(
          [
            { key: 'key', label: 'Check' },
            { key: 'value', label: 'State' },
          ],
          rows
        )
      );
    } catch (err) {
      readyBox.appendChild(h('div', { class: 'muted small', text: friendlyError(err) }));
    }
  }

  async function loadStatus() {
    clear(statusBox);
    try {
      const data = await request('GET', '/status');
      const rows = flatten(data, '').slice(0, 18);
      statusBox.appendChild(
        table(
          [
            { key: 'key', label: 'Metric' },
            { key: 'value', label: 'Value' },
          ],
          rows
        )
      );
    } catch (err) {
      const hint =
        err.status === 401 || err.status === 403
          ? 'System status needs an admin key.'
          : friendlyError(err);
      statusBox.appendChild(h('div', { class: 'muted small', text: hint }));
    }
  }

  const refresh = h('button', { class: 'btn-ghost', text: 'Refresh' });
  refresh.addEventListener('click', () => {
    loadReadiness();
    loadStatus();
  });

  clear(root).appendChild(
    h('section', { class: 'panel' }, [
      h('div', { class: 'panel-head' }, [h('h2', { text: 'Overview' }), refresh]),
      h('h3', { class: 'subhead', text: 'Readiness' }),
      readyBox,
      h('h3', { class: 'subhead', text: 'System status' }),
      statusBox,
    ])
  );
  loadReadiness();
  loadStatus();
}
