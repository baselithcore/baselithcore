// Classic script: entry point. Depends on admin-utils.js and admin-data.js,
// so it must be loaded last.

async function reindexDocs() {
  try {
    const res = await fetch('/reindex', { method: 'POST' });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    alert('Reindexing complete ✅ New files: ' + data.new_files_indexed);
    await loadData();
  } catch (err) {
    console.error('[admin] Failed to reindex documents:', err);
    alert('❌ Reindexing failed: ' + (err && err.message ? err.message : err));
  }
}

function openModal(text) {
  document.getElementById('modalText').textContent = text;
  document.getElementById('modal').style.display = 'block';
}

function closeModal() {
  document.getElementById('modal').style.display = 'none';
}

window.onclick = function (event) {
  const modal = document.getElementById('modal');
  if (event.target === modal) closeModal();
};

function switchTab(evt) {
  const tabButtons = document.querySelectorAll('.tab-button');
  tabButtons.forEach((btn) => btn.classList.remove('active'));
  evt.currentTarget.classList.add('active');

  const contents = document.querySelectorAll('.tab-content');
  contents.forEach((c) => (c.style.display = 'none'));

  const tabId = evt.currentTarget.getAttribute('data-tab');
  document.getElementById(tabId).style.display = 'block';

  if (tabId === 'analyticsTab') {
    loadData().catch((err) => console.error('[admin] Failed to refresh analytics:', err));
  }
}

function toggleDarkMode() {
  document.body.classList.toggle('dark-mode');
  const btn = document.querySelector('.btn-toggle-mode');
  if (btn) {
    btn.textContent = document.body.classList.contains('dark-mode') ? '☀️' : '🌙';
  }
}

function setupAnalyticsFilters() {
  const select = document.getElementById('timeRangeSelect');
  if (!select) {
    return;
  }
  select.addEventListener('change', (event) => {
    const value = event.target.value;
    if (value === 'all') {
      loadData({ days: null }).catch((err) =>
        console.error('[admin] Failed to load analytics:', err)
      );
      return;
    }
    const parsed = Number.parseInt(value, 10);
    if (Number.isFinite(parsed) && parsed > 0) {
      loadData({ days: parsed }).catch((err) =>
        console.error('[admin] Failed to load analytics:', err)
      );
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  setupAnalyticsFilters();
  loadData().catch((err) => console.error('[admin] Failed to load analytics:', err));
});
