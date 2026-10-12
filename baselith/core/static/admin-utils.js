// Classic script: shares the global scope with admin-data.js and admin.js,
// both of which read the bindings declared here. Must be loaded first.

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
