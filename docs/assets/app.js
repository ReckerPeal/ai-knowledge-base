(() => {
  'use strict';

  const DATA_URL = 'data/index.json';
  const TOP_TAGS = 30;

  const state = {
    all: [],
    filtered: [],
    query: '',
    minScore: 0,
    sort: 'date',
    selectedTags: new Set(),
    dateFilter: '',
    showAllTags: false,
  };

  const $ = (id) => document.getElementById(id);
  const els = {
    q: $('q'),
    minScore: $('min-score'),
    scoreValue: $('score-value'),
    sort: $('sort'),
    dateFilter: $('date-filter'),
    reset: $('reset'),
    tags: $('tags'),
    toggleTags: $('toggle-tags'),
    list: $('list'),
    status: $('status'),
    total: $('total'),
  };

  init();

  async function init() {
    bindEvents();
    try {
      const res = await fetch(DATA_URL, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.all = Array.isArray(data) ? data : [];
      els.total.textContent = String(state.all.length);
      buildDateFilter(state.all);
      buildTagCloud(state.all);
      apply();
    } catch (err) {
      els.status.textContent = `加载失败：${err.message}。请确认 data/index.json 已生成。`;
    }
  }

  function bindEvents() {
    els.q.addEventListener('input', (e) => {
      state.query = e.target.value.trim().toLowerCase();
      apply();
    });
    els.minScore.addEventListener('input', (e) => {
      state.minScore = Number(e.target.value);
      els.scoreValue.textContent = String(state.minScore);
      apply();
    });
    els.sort.addEventListener('change', (e) => {
      state.sort = e.target.value;
      apply();
    });
    els.dateFilter.addEventListener('change', (e) => {
      state.dateFilter = e.target.value;
      apply();
    });
    els.reset.addEventListener('click', () => {
      state.query = '';
      state.minScore = 0;
      state.sort = 'date';
      state.dateFilter = '';
      state.selectedTags.clear();
      els.q.value = '';
      els.minScore.value = '0';
      els.scoreValue.textContent = '0';
      els.sort.value = 'date';
      els.dateFilter.value = '';
      renderTagCloud();
      apply();
    });
    els.toggleTags.addEventListener('click', () => {
      state.showAllTags = !state.showAllTags;
      els.toggleTags.textContent = state.showAllTags ? '收起' : '展开全部';
      els.tags.classList.toggle('collapsed', !state.showAllTags);
    });
  }

  function buildDateFilter(items) {
    const dates = new Set();
    for (const it of items) {
      const d = (it.collected_at || '').slice(0, 10);
      if (d) dates.add(d);
    }
    const sorted = [...dates].sort().reverse();
    for (const d of sorted) {
      const opt = document.createElement('option');
      opt.value = d;
      opt.textContent = d;
      els.dateFilter.appendChild(opt);
    }
  }

  function buildTagCloud(items) {
    const counts = new Map();
    for (const it of items) {
      for (const t of it.tags || []) {
        counts.set(t, (counts.get(t) || 0) + 1);
      }
    }
    state.tagCounts = [...counts.entries()].sort((a, b) => b[1] - a[1]);
    els.tags.classList.add('collapsed');
    renderTagCloud();
  }

  function renderTagCloud() {
    els.tags.innerHTML = '';
    const list = state.tagCounts || [];
    const visible = state.showAllTags ? list : list.slice(0, TOP_TAGS);
    for (const [name, count] of visible) {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'tag' + (state.selectedTags.has(name) ? ' active' : '');
      el.innerHTML = `${escapeHtml(name)} <span class="count">${count}</span>`;
      el.addEventListener('click', () => {
        if (state.selectedTags.has(name)) state.selectedTags.delete(name);
        else state.selectedTags.add(name);
        renderTagCloud();
        apply();
      });
      els.tags.appendChild(el);
    }
    if (list.length > TOP_TAGS) {
      els.toggleTags.hidden = false;
    } else {
      els.toggleTags.hidden = true;
    }
  }

  function apply() {
    const q = state.query;
    const min = state.minScore;
    const dateFilter = state.dateFilter;
    const selectedTags = state.selectedTags;

    let out = state.all.filter((it) => {
      const score = Number(it.score || 0);
      if (score < min) return false;
      if (dateFilter && !(it.collected_at || '').startsWith(dateFilter)) return false;
      if (selectedTags.size > 0) {
        const tags = new Set(it.tags || []);
        for (const t of selectedTags) if (!tags.has(t)) return false;
      }
      if (q) {
        const hay = [
          it.title || '',
          it.summary || '',
          (it.tags || []).join(' '),
        ].join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    if (state.sort === 'score') {
      out.sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
    } else if (state.sort === 'stars') {
      out.sort((a, b) => Number((b.metadata || {}).stars || 0) - Number((a.metadata || {}).stars || 0));
    } else {
      out.sort((a, b) => String(b.collected_at || '').localeCompare(String(a.collected_at || '')));
    }

    state.filtered = out;
    render();
  }

  function render() {
    const items = state.filtered;
    els.status.textContent = items.length === 0
      ? '没有匹配的文章。'
      : `命中 ${items.length} / ${state.all.length} 条`;
    els.list.innerHTML = '';
    const frag = document.createDocumentFragment();
    for (const it of items) frag.appendChild(card(it));
    els.list.appendChild(frag);
  }

  function card(it) {
    const li = document.createElement('li');
    li.className = 'card';

    const score = Number(it.score || 0);
    const scoreClass = score >= 8 ? 'score-good' : score >= 6 ? 'score-mid' : 'score-low';
    const date = (it.collected_at || '').slice(0, 10);
    const stars = (it.metadata || {}).stars;
    const url = `article.html?id=${encodeURIComponent(it.id)}`;

    const tags = (it.tags || []).slice(0, 5)
      .map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join('');

    li.innerHTML = `
      <h3><a class="title-link" href="${url}">${escapeHtml(it.title || '(untitled)')}</a></h3>
      <p class="summary">${escapeHtml(it.summary || '')}</p>
      <div class="tag-list">${tags}</div>
      <div class="meta">
        <span class="badge badge-score ${scoreClass}">★ ${score.toFixed(1)}</span>
        ${stars ? `<span>⭐ ${formatNumber(stars)}</span>` : ''}
        ${it.language ? `<span>${escapeHtml(it.language)}</span>` : ''}
        ${date ? `<span>${date}</span>` : ''}
      </div>
    `;
    return li;
  }

  function formatNumber(n) {
    n = Number(n);
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    return String(n);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }
})();
