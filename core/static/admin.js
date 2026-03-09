let feedbackChartObj = null;
let lineChartObj = null;

const DEFAULT_ANALYTICS_PARAMS = Object.freeze({
  days: 30,
  recent_limit: 20,
  top_limit: 10,
});
let analyticsParams = { ...DEFAULT_ANALYTICS_PARAMS };

const PERCENT_FORMATTER =
  typeof Intl !== 'undefined'
    ? new Intl.NumberFormat('en-US', {
        maximumFractionDigits: 1,
        minimumFractionDigits: 0,
      })
    : null;
const DATETIME_FORMATTER =
  typeof Intl !== 'undefined'
    ? new Intl.NumberFormat('en-US', {
        dateStyle: 'short',
        timeStyle: 'short',
      })
    : null;

function formatPercentage(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '0%';
  }
  const normalized = Math.max(0, Math.min(1, value));
  const percent = normalized * 100;
  if (PERCENT_FORMATTER) {
    return `${PERCENT_FORMATTER.format(percent)}%`;
  }
  const precision = percent >= 10 ? 0 : 1;
  return `${percent.toFixed(precision)}%`;
}

function formatDateTime(value) {
  if (!value) {
    return '—';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  if (DATETIME_FORMATTER) {
    return DATETIME_FORMATTER.format(parsed);
  }
  return parsed.toISOString();
}

function updateClarificationMetrics(metrics) {
  const triggeredEl = document.getElementById('clarificationTriggered');
  const noHitsEl = document.getElementById('clarificationNoHits');
  const noRerankedEl = document.getElementById('clarificationNoReranked');
  const emptyContextEl = document.getElementById('clarificationEmptyContext');

  if (!triggeredEl || !noHitsEl || !noRerankedEl || !emptyContextEl) {
    return;
  }

  const defaults = {
    triggered: 0,
    no_hits: 0,
    no_reranked_hits: 0,
    empty_context: 0,
  };
  const safeMetrics = { ...defaults, ...(metrics || {}) };
  const sanitize = (value) => (typeof value === 'number' && Number.isFinite(value) ? value : 0);

  triggeredEl.textContent = sanitize(safeMetrics.triggered);
  noHitsEl.textContent = sanitize(safeMetrics.no_hits);
  noRerankedEl.textContent = sanitize(safeMetrics.no_reranked_hits);
  emptyContextEl.textContent = sanitize(safeMetrics.empty_context);
}

function summarizeSources(sources) {
  if (!Array.isArray(sources) || !sources.length) {
    return { text: '—', tooltip: 'No associated source' };
  }

  const labels = [];
  const tooltips = [];

  sources.forEach((src) => {
    if (!src) {
      return;
    }
    if (typeof src === 'object') {
      const label = src.title || src.path || src.url || src.document_id;
      if (typeof label === 'string' && label.trim().length) {
        labels.push(label.trim());
      }
      try {
        tooltips.push(JSON.stringify(src, null, 2));
      } catch (err) {
        tooltips.push(String(src));
      }
    } else if (typeof src === 'string') {
      const trimmed = src.trim();
      if (trimmed.length) {
        labels.push(trimmed);
        tooltips.push(trimmed);
      }
    }
  });

  const tooltip = tooltips.filter((item) => item && item.trim().length).join('\n\n');

  if (!labels.length) {
    const fallback = tooltip || 'No source available';
    const text = fallback.length > 60 ? `${fallback.slice(0, 60)}…` : fallback;
    return { text, tooltip: fallback };
  }

  if (labels.length === 1) {
    return { text: labels[0], tooltip: tooltip || labels[0] };
  }

  const [first, ...rest] = labels;
  return {
    text: `${first} (+${rest.length})`,
    tooltip: tooltip || labels.join('\n'),
  };
}

function formatFeedbackLabel(value) {
  if (value === 'positive') {
    return 'Positive';
  }
  if (value === 'negative') {
    return 'Negative';
  }
  return value || '—';
}

function updateWindowLabels(windowInfo) {
  let label = 'Full history';
  if (windowInfo && typeof windowInfo.days === 'number' && windowInfo.days > 0) {
    if (windowInfo.days === 1) {
      label = 'Last day';
    } else {
      label = `Last ${windowInfo.days} days`;
    }
  }
  document.querySelectorAll('[data-window-label]').forEach((el) => {
    el.textContent = label;
  });
}

async function loadData(overrides = {}) {
  analyticsParams = { ...analyticsParams, ...overrides };

  const searchParams = new URLSearchParams();
  if (analyticsParams.days !== null && analyticsParams.days !== undefined) {
    searchParams.set('days', analyticsParams.days);
  }
  if (analyticsParams.recent_limit !== null && analyticsParams.recent_limit !== undefined) {
    searchParams.set('recent_limit', analyticsParams.recent_limit);
  }
  if (analyticsParams.top_limit !== null && analyticsParams.top_limit !== undefined) {
    searchParams.set('top_limit', analyticsParams.top_limit);
  }

  const queryString = searchParams.toString();
  const url = queryString.length > 0 ? `/admin/data?${queryString}` : '/admin/data';

  updateClarificationMetrics();
  const statusPromise = fetch('/status')
    .then((res) => (res.ok ? res.json() : null))
    .catch((err) => {
      console.error('[admin] Failed to load status:', err);
      return null;
    });

  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }

  const data = await res.json();
  const statusData = await statusPromise;
  const clarificationData =
    statusData && statusData.metrics ? statusData.metrics.clarification : null;
  updateClarificationMetrics(clarificationData);

  updateWindowLabels(data.window || null);

  const totalEl = document.getElementById('total');
  if (totalEl) totalEl.textContent = data.total_feedbacks ?? 0;

  const positiveEl = document.getElementById('positives');
  if (positiveEl) positiveEl.textContent = data.positives ?? 0;

  const negativeEl = document.getElementById('negatives');
  if (negativeEl) negativeEl.textContent = data.negatives ?? 0;

  const positiveRateEl = document.getElementById('positiveRate');
  if (positiveRateEl) positiveRateEl.textContent = formatPercentage(data.positive_rate);

  const timeRangeSelect = document.getElementById('timeRangeSelect');
  if (timeRangeSelect) {
    const value =
      analyticsParams.days === null || analyticsParams.days === undefined
        ? 'all'
        : String(analyticsParams.days);
    if (timeRangeSelect.value !== value) {
      timeRangeSelect.value = value;
    }
  }

  const donutCtx = document.getElementById('feedbackChart');
  if (donutCtx && typeof Chart !== 'undefined') {
    if (feedbackChartObj) feedbackChartObj.destroy();
    feedbackChartObj = new Chart(donutCtx, {
      type: 'doughnut',
      data: {
        labels: ['Positive', 'Negative'],
        datasets: [
          {
            data: [data.positives || 0, data.negatives || 0],
            backgroundColor: ['#10b981', '#ef4444'],
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { position: 'bottom' } },
      },
    });
  }

  const recentItems = Array.isArray(data.recent) ? data.recent : [];
  const tbody = document.getElementById('feedbackTable');
  if (tbody) {
    tbody.innerHTML = '';
    if (!recentItems.length) {
      const emptyRow = document.createElement('tr');
      const emptyCell = document.createElement('td');
      emptyCell.colSpan = 8;
      emptyCell.textContent = 'No feedback recorded in the selected period.';
      emptyRow.appendChild(emptyCell);
      tbody.appendChild(emptyRow);
    } else {
      recentItems.forEach((item) => {
        const tr = document.createElement('tr');

        const idCell = document.createElement('td');
        idCell.textContent = item.id ?? '—';
        tr.appendChild(idCell);

        const queryCell = document.createElement('td');
        queryCell.textContent = item.query || '—';
        tr.appendChild(queryCell);

        const answerCell = document.createElement('td');
        const rawAnswer = typeof item.answer === 'string' ? item.answer : '';
        const trimmedAnswer = rawAnswer.length > 120 ? `${rawAnswer.slice(0, 120)}…` : rawAnswer;
        const answerSpan = document.createElement('span');
        answerSpan.textContent = trimmedAnswer || '—';
        answerCell.appendChild(answerSpan);
        if (rawAnswer) {
          const answerBtn = document.createElement('button');
          answerBtn.type = 'button';
          answerBtn.className = 'btn small-btn';
          answerBtn.title = 'Mostra risposta completa';
          answerBtn.textContent = '📄';
          answerBtn.addEventListener('click', () => openModal(rawAnswer));
          answerCell.appendChild(answerBtn);
        }
        tr.appendChild(answerCell);
        const conversationCell = document.createElement('td');
        const convId =
          typeof item.conversation_id === 'string' && item.conversation_id.trim().length
            ? item.conversation_id.trim()
            : null;
        conversationCell.textContent = convId || '—';
        if (convId) {
          conversationCell.title = convId;
        }
        tr.appendChild(conversationCell);

        const commentCell = document.createElement('td');
        const commentText = typeof item.comment === 'string' ? item.comment.trim() : '';
        if (commentText.length) {
          const trimmedComment =
            commentText.length > 160 ? `${commentText.slice(0, 160)}…` : commentText;
          commentCell.textContent = trimmedComment;
          if (trimmedComment !== commentText) {
            commentCell.title = commentText;
          }
        } else {
          commentCell.textContent = '—';
        }
        tr.appendChild(commentCell);

        const sourcesCell = document.createElement('td');
        const summary = summarizeSources(item.sources);
        sourcesCell.textContent = summary.text;
        if (summary.tooltip) {
          sourcesCell.title = summary.tooltip;
        }
        tr.appendChild(sourcesCell);

        const timestampCell = document.createElement('td');
        timestampCell.textContent = formatDateTime(item.timestamp);
        if (item.timestamp) {
          timestampCell.title = item.timestamp;
        }
        tr.appendChild(timestampCell);

        tbody.appendChild(tr);
      });
    }
  }

  const timeseries = Array.isArray(data.timeseries) ? data.timeseries : [];
  const labels = timeseries.map((row) => row.date);
  const positivesSeries = timeseries.map((row) => row.positives || 0);
  const negativesSeries = timeseries.map((row) => row.negatives || 0);

  const lineCtx = document.getElementById('lineChart');
  if (lineCtx && typeof Chart !== 'undefined') {
    if (lineChartObj) lineChartObj.destroy();
    lineChartObj = new Chart(lineCtx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Positive',
            data: positivesSeries,
            borderColor: '#10b981',
            backgroundColor: 'rgba(16, 185, 129, 0.2)',
            tension: 0.3,
            fill: false,
            pointRadius: 3,
          },
          {
            label: 'Negative',
            data: negativesSeries,
            borderColor: '#ef4444',
            backgroundColor: 'rgba(239, 68, 68, 0.2)',
            tension: 0.3,
            fill: false,
            pointRadius: 3,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { title: { display: true, text: 'Date' } },
          y: {
            title: { display: true, text: 'Feedback count' },
            beginAtZero: true,
            ticks: { precision: 0 },
          },
        },
      },
    });
  }

  const topQueries = Array.isArray(data.top_queries) ? data.top_queries : [];
  const queriesBody = document.getElementById('topQueriesBody');
  if (queriesBody) {
    queriesBody.innerHTML = '';
    if (!topQueries.length) {
      const emptyRow = document.createElement('tr');
      const emptyCell = document.createElement('td');
      emptyCell.colSpan = 6;
      emptyCell.textContent = 'No queries available for the selected period.';
      emptyCell.className = 'empty-state';
      emptyRow.appendChild(emptyCell);
      queriesBody.appendChild(emptyRow);
    } else {
      topQueries.forEach((item) => {
        const tr = document.createElement('tr');

        const queryCell = document.createElement('td');
        queryCell.textContent = item.query || '—';
        tr.appendChild(queryCell);

        const totalCell = document.createElement('td');
        totalCell.textContent = (item.total ?? 0).toString();
        tr.appendChild(totalCell);

        const positiveCell = document.createElement('td');
        positiveCell.textContent = (item.positives ?? 0).toString();
        tr.appendChild(positiveCell);

        const negativeCell = document.createElement('td');
        negativeCell.textContent = (item.negatives ?? 0).toString();
        tr.appendChild(negativeCell);

        const rateCell = document.createElement('td');
        rateCell.textContent = formatPercentage(item.positive_rate);
        tr.appendChild(rateCell);

        const lastCell = document.createElement('td');
        lastCell.textContent = formatDateTime(item.last_timestamp);
        if (item.last_timestamp) {
          lastCell.title = item.last_timestamp;
        }
        tr.appendChild(lastCell);

        queriesBody.appendChild(tr);
      });
    }
  }

  const topDocuments = Array.isArray(data.top_documents) ? data.top_documents : [];
  const docsBody = document.getElementById('topDocsBody');
  if (docsBody) {
    docsBody.innerHTML = '';
    if (!topDocuments.length) {
      const emptyRow = document.createElement('tr');
      const emptyCell = document.createElement('td');
      emptyCell.colSpan = 6;
      emptyCell.textContent = 'No sources available for the selected period.';
      emptyCell.className = 'empty-state';
      emptyRow.appendChild(emptyCell);
      docsBody.appendChild(emptyRow);
    } else {
      topDocuments.forEach((doc) => {
        const tr = document.createElement('tr');

        const labelCell = document.createElement('td');
        if (doc.url) {
          const link = document.createElement('a');
          link.href = doc.url;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          link.textContent = doc.title || doc.url;
          labelCell.appendChild(link);
        } else if (doc.path) {
          const code = document.createElement('code');
          code.textContent = doc.path;
          labelCell.appendChild(code);
        } else {
          labelCell.textContent = doc.title || doc.document_id || '—';
        }
        tr.appendChild(labelCell);

        const originCell = document.createElement('td');
        originCell.textContent = doc.origin || doc.source_type || '—';
        tr.appendChild(originCell);

        const totalCell = document.createElement('td');
        totalCell.textContent = (doc.total ?? 0).toString();
        tr.appendChild(totalCell);

        const positivesCell = document.createElement('td');
        positivesCell.textContent = (doc.positives ?? 0).toString();
        tr.appendChild(positivesCell);

        const negativesCell = document.createElement('td');
        negativesCell.textContent = (doc.negatives ?? 0).toString();
        tr.appendChild(negativesCell);

        const rateCell = document.createElement('td');
        rateCell.textContent = formatPercentage(doc.positive_rate);
        tr.appendChild(rateCell);

        docsBody.appendChild(tr);
      });
    }
  }

  const learningItems = Array.isArray(data.learning_candidates) ? data.learning_candidates : [];
  const learningBody = document.getElementById('learningBody');
  if (learningBody) {
    learningBody.innerHTML = '';
    if (!learningItems.length) {
      const emptyRow = document.createElement('tr');
      const emptyCell = document.createElement('td');
      emptyCell.colSpan = 6;
      emptyCell.textContent = 'No queries require attention in the selected period.';
      emptyCell.className = 'empty-state';
      emptyRow.appendChild(emptyCell);
      learningBody.appendChild(emptyRow);
    } else {
      learningItems.forEach((item) => {
        const tr = document.createElement('tr');

        const queryCell = document.createElement('td');
        queryCell.textContent = item.query || '—';
        tr.appendChild(queryCell);

        const totalCell = document.createElement('td');
        totalCell.textContent = (item.total ?? 0).toString();
        tr.appendChild(totalCell);

        const positiveCell = document.createElement('td');
        positiveCell.textContent = (item.positives ?? 0).toString();
        tr.appendChild(positiveCell);

        const negativeCell = document.createElement('td');
        negativeCell.textContent = (item.negatives ?? 0).toString();
        tr.appendChild(negativeCell);

        const rateCell = document.createElement('td');
        rateCell.textContent = formatPercentage(item.positive_rate);
        tr.appendChild(rateCell);

        const lastCell = document.createElement('td');
        lastCell.textContent = formatDateTime(item.last_timestamp);
        if (item.last_timestamp) {
          lastCell.title = item.last_timestamp;
        }
        tr.appendChild(lastCell);

        learningBody.appendChild(tr);
      });
    }
  }
}

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
