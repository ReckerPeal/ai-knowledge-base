(() => {
  'use strict';

  const TOP_TAGS = 30;
  const MAX_RENDER = 200;
  const TODAY_PER_GROUP = 6;
  const MODES = ['today', 'history', 'all'];

  const state = {
    mode: 'today',
    dates: [],            // [{date, count}, ...] desc
    selectedDate: null,   // history-mode date
    today: null,          // latest date from dates.json
    perDateCache: {},     // date -> array of articles
    allArticles: [],      // index.json
    items: [],            // current view's source list
    filtered: [],
    query: '',
    minScore: 0,
    sort: 'daily_stars',
    selectedTags: new Set(),
    selectedCategory: '',  // '' means all
    showAllTags: false,
    tagCounts: [],
    categoryCounts: [],
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
    categories: $('categories'),
    list: $('list'),
    grouped: $('grouped'),
    status: $('status'),
    banner: $('today-banner'),
    tabButtons: document.querySelectorAll('.tab'),
    historyOnly: document.querySelectorAll('.history-only'),
    allOnly: document.querySelectorAll('.all-only'),
  };

  init();

  async function init() {
    bindEvents();
    parseHash();
    try {
      const dates = await fetchJSON('data/dates.json');
      state.dates = Array.isArray(dates) ? dates : [];
      state.today = state.dates[0]?.date || null;
      buildDateSelect();
    } catch (err) {
      els.status.textContent = `加载日期列表失败：${err.message}`;
      return;
    }
    await loadCurrentMode();
  }

  function bindEvents() {
    els.tabButtons.forEach((btn) => {
      btn.addEventListener('click', () => switchMode(btn.dataset.mode));
    });
    window.addEventListener('hashchange', () => {
      parseHash();
      applyModeUI();
      loadCurrentMode();
    });

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
      state.selectedDate = e.target.value;
      writeHash();
      loadCurrentMode();
    });
    els.reset.addEventListener('click', () => {
      state.query = '';
      state.minScore = 0;
      state.selectedTags.clear();
      state.selectedCategory = '';
      els.q.value = '';
      els.minScore.value = '0';
      els.scoreValue.textContent = '0';
      state.sort = defaultSortFor(state.mode);
      els.sort.value = state.sort;
      renderTagCloud();
      renderCategoryCloud();
      apply();
    });
    els.toggleTags.addEventListener('click', () => {
      state.showAllTags = !state.showAllTags;
      els.toggleTags.textContent = state.showAllTags ? '收起' : '展开全部';
      els.tags.classList.toggle('collapsed', !state.showAllTags);
    });
  }

  function parseHash() {
    const raw = location.hash.replace(/^#\/?/, '');
    const [mode, arg] = raw.split('/');
    if (MODES.includes(mode)) {
      state.mode = mode;
      if (mode === 'history' && arg) state.selectedDate = arg;
    } else {
      state.mode = 'today';
    }
    state.sort = defaultSortFor(state.mode);
    applyModeUI();
  }

  function writeHash() {
    let h = `#/${state.mode}`;
    if (state.mode === 'history' && state.selectedDate) {
      h += `/${state.selectedDate}`;
    }
    if (location.hash !== h) {
      history.replaceState(null, '', h);
    }
  }

  function switchMode(mode) {
    if (!MODES.includes(mode)) return;
    if (mode === state.mode) {
      // Allow re-render so category jump from "查看全部" button works.
      writeHash();
      applyModeUI();
      apply();
      return;
    }
    state.mode = mode;
    state.sort = defaultSortFor(mode);
    state.query = '';
    state.minScore = 0;
    state.selectedTags.clear();
    els.q.value = '';
    els.minScore.value = '0';
    els.scoreValue.textContent = '0';
    if (mode === 'history' && !state.selectedDate && state.dates.length > 0) {
      state.selectedDate = state.dates[0].date;
      els.dateFilter.value = state.selectedDate;
    }
    writeHash();
    applyModeUI();
    loadCurrentMode();
  }

  function applyModeUI() {
    els.tabButtons.forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.mode === state.mode);
      btn.setAttribute('aria-selected', btn.dataset.mode === state.mode ? 'true' : 'false');
    });
    els.historyOnly.forEach((el) => { el.hidden = state.mode !== 'history'; });
    els.allOnly.forEach((el) => { el.hidden = state.mode !== 'all'; });
    els.sort.value = state.sort;
  }

  function defaultSortFor(mode) {
    if (mode === 'today' || mode === 'history') return 'daily_stars';
    return 'date';
  }

  function buildDateSelect() {
    els.dateFilter.innerHTML = '';
    for (const { date, count } of state.dates) {
      const opt = document.createElement('option');
      opt.value = date;
      opt.textContent = `${date}（${count}）`;
      els.dateFilter.appendChild(opt);
    }
    if (state.mode === 'history') {
      if (!state.selectedDate || !state.dates.find((d) => d.date === state.selectedDate)) {
        state.selectedDate = state.dates[0]?.date || null;
      }
      if (state.selectedDate) els.dateFilter.value = state.selectedDate;
    }
  }

  async function loadCurrentMode() {
    if (state.mode === 'today') {
      if (!state.today) {
        els.status.textContent = '暂无任何采集数据。';
        state.items = [];
        render();
        return;
      }
      state.items = await loadByDate(state.today);
      showTodayBanner();
    } else if (state.mode === 'history') {
      if (!state.selectedDate) {
        state.items = [];
        render();
        return;
      }
      state.items = await loadByDate(state.selectedDate);
      hideBanner();
    } else {
      state.items = await loadAll();
      hideBanner();
      buildTagCloud(state.items);
    }
    buildCategoryCloud(state.items);
    apply();
  }

  function showTodayBanner() {
    const now = new Date();
    const todayStr = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    if (state.today && state.today !== todayStr) {
      els.banner.hidden = false;
      els.banner.textContent = `今日（${todayStr}）暂未采集，展示最近一天 ${state.today} 的数据。`;
    } else {
      hideBanner();
    }
  }

  function hideBanner() {
    els.banner.hidden = true;
    els.banner.textContent = '';
  }

  async function loadByDate(date) {
    if (state.perDateCache[date]) return state.perDateCache[date];
    try {
      const data = await fetchJSON(`data/by_date/${date}.json`);
      const list = Array.isArray(data) ? data : [];
      state.perDateCache[date] = list;
      return list;
    } catch (err) {
      els.status.textContent = `加载 ${date} 数据失败：${err.message}`;
      return [];
    }
  }

  async function loadAll() {
    if (state.allArticles.length > 0) return state.allArticles;
    try {
      const data = await fetchJSON('data/index.json');
      state.allArticles = Array.isArray(data) ? data : [];
      return state.allArticles;
    } catch (err) {
      els.status.textContent = `加载全量索引失败：${err.message}`;
      return [];
    }
  }

  async function fetchJSON(url) {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
    return res.json();
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
    state.showAllTags = false;
    els.toggleTags.textContent = '展开全部';
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
    els.toggleTags.hidden = list.length <= TOP_TAGS;
  }

  function buildCategoryCloud(items) {
    const counts = new Map();
    for (const it of items) {
      const cat = String((it.metadata || {}).category || '其他').trim() || '其他';
      counts.set(cat, (counts.get(cat) || 0) + 1);
    }
    state.categoryCounts = [...counts.entries()].sort((a, b) => b[1] - a[1]);
    if (state.selectedCategory && !counts.has(state.selectedCategory)) {
      state.selectedCategory = '';
    }
    renderCategoryCloud();
  }

  function renderCategoryCloud() {
    if (!els.categories) return;
    els.categories.innerHTML = '';
    const allBtn = document.createElement('button');
    allBtn.type = 'button';
    allBtn.className = 'cat-pill' + (state.selectedCategory === '' ? ' active' : '');
    allBtn.innerHTML = `全部 <span class="count">${state.items.length}</span>`;
    allBtn.addEventListener('click', () => {
      state.selectedCategory = '';
      renderCategoryCloud();
      apply();
    });
    els.categories.appendChild(allBtn);

    for (const [name, count] of state.categoryCounts) {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'cat-pill' + (state.selectedCategory === name ? ' active' : '');
      el.dataset.kind = categoryKind(name);
      el.innerHTML = `${escapeHtml(name)} <span class="count">${count}</span>`;
      el.addEventListener('click', () => {
        state.selectedCategory = state.selectedCategory === name ? '' : name;
        renderCategoryCloud();
        apply();
      });
      els.categories.appendChild(el);
    }
  }

  function categoryKind(name) {
    if (!name) return 'other';
    if (name.startsWith('GitHub')) return 'github';
    if (name.includes('博客')) return 'blog';
    if (name.includes('研究')) return 'research';
    if (name.includes('中文')) return 'cn';
    if (name.includes('综合')) return 'general';
    return 'other';
  }

  function apply() {
    const q = state.query;
    const min = state.minScore;
    const selectedTags = state.selectedTags;
    const selectedCategory = state.selectedCategory;

    let out = state.items.filter((it) => {
      if (state.mode === 'all') {
        const score = Number(it.score || 0);
        if (score < min) return false;
      }
      if (selectedCategory) {
        const cat = String((it.metadata || {}).category || '其他') || '其他';
        if (cat !== selectedCategory) return false;
      }
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

    out.sort(sorter(state.sort));
    state.filtered = out;
    render();
  }

  function sorter(by) {
    if (by === 'score') return (a, b) => Number(b.score || 0) - Number(a.score || 0);
    if (by === 'stars') return (a, b) =>
      Number((b.metadata || {}).stars || 0) - Number((a.metadata || {}).stars || 0);
    if (by === 'daily_stars') return (a, b) => {
      const da = numericOrNeg(((a.metadata || {}).daily_stars));
      const db = numericOrNeg(((b.metadata || {}).daily_stars));
      return db - da;
    };
    return (a, b) => String(b.collected_at || '').localeCompare(String(a.collected_at || ''));
  }

  function numericOrNeg(v) {
    return typeof v === 'number' ? v : Number.NEGATIVE_INFINITY;
  }

  function render() {
    const items = state.filtered;
    const total = state.items.length;
    const labelByMode = {
      today: '今日',
      history: state.selectedDate || '历史',
      all: '全部',
    };

    if (items.length === 0) {
      els.status.textContent = total === 0 ? '暂无数据。' : '没有匹配的文章。';
    } else if (state.mode === 'all' && items.length > MAX_RENDER) {
      els.status.textContent =
        `${labelByMode[state.mode]}：命中 ${items.length} / ${total} 条，` +
        `先展示前 ${MAX_RENDER} 条，输入关键词或筛选可缩小范围`;
    } else {
      els.status.textContent = `${labelByMode[state.mode]}：命中 ${items.length} / ${total} 条`;
    }

    if (state.mode === 'today') {
      renderGrouped(items);
    } else {
      renderFlat(items);
    }
  }

  function renderFlat(items) {
    els.grouped.hidden = true;
    els.grouped.innerHTML = '';
    els.list.hidden = false;
    els.list.innerHTML = '';
    const frag = document.createDocumentFragment();
    const visible = items.length > MAX_RENDER ? items.slice(0, MAX_RENDER) : items;
    for (const it of visible) frag.appendChild(card(it));
    els.list.appendChild(frag);
  }

  function renderGrouped(items) {
    els.list.hidden = true;
    els.list.innerHTML = '';
    els.grouped.hidden = false;
    els.grouped.innerHTML = '';

    if (items.length === 0) return;

    // Group by metadata.category, sorted within each group by current sort.
    const buckets = new Map();
    for (const it of items) {
      const cat = String((it.metadata || {}).category || '其他') || '其他';
      if (!buckets.has(cat)) buckets.set(cat, []);
      buckets.get(cat).push(it);
    }

    // Order categories by total daily_stars then by score sum.
    const ordered = [...buckets.entries()].sort((a, b) => {
      const sa = a[1].reduce((acc, it) => acc + (((it.metadata || {}).daily_stars) || 0), 0);
      const sb = b[1].reduce((acc, it) => acc + (((it.metadata || {}).daily_stars) || 0), 0);
      if (sb !== sa) return sb - sa;
      return b[1].length - a[1].length;
    });

    const frag = document.createDocumentFragment();
    for (const [cat, bucket] of ordered) {
      bucket.sort(sorter(state.sort));
      const visible = bucket.slice(0, TODAY_PER_GROUP);
      const more = bucket.length - visible.length;
      const section = document.createElement('section');
      section.className = 'cat-row';
      section.dataset.kind = categoryKind(cat);

      const header = document.createElement('header');
      header.className = 'cat-row-header';
      const h2 = document.createElement('h2');
      h2.className = 'cat-row-title';
      h2.textContent = cat;
      header.appendChild(h2);

      const count = document.createElement('span');
      count.className = 'cat-row-count';
      count.textContent = `${bucket.length} 条`;
      header.appendChild(count);

      if (more > 0) {
        const moreBtn = document.createElement('button');
        moreBtn.type = 'button';
        moreBtn.className = 'btn-text';
        moreBtn.textContent = `查看全部 ${bucket.length} 条 →`;
        moreBtn.addEventListener('click', () => {
          state.selectedCategory = cat;
          switchMode('all');
        });
        header.appendChild(moreBtn);
      }

      section.appendChild(header);
      const ul = document.createElement('ul');
      ul.className = 'card-grid cat-row-cards';
      for (const it of visible) ul.appendChild(card(it));
      section.appendChild(ul);
      frag.appendChild(section);
    }
    els.grouped.appendChild(frag);
  }

  function card(it) {
    const li = document.createElement('li');
    li.className = 'card';

    const score = Number(it.score || 0);
    const scoreClass = score >= 8 ? 'score-good' : score >= 6 ? 'score-mid' : 'score-low';
    const date = (it.collected_at || '').slice(0, 10);
    const m = it.metadata || {};
    const stars = m.stars;
    const daily = m.daily_stars;
    const url = `article.html?id=${encodeURIComponent(it.id)}&date=${encodeURIComponent(date)}`;

    const tags = (it.tags || []).slice(0, 5)
      .map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join('');

    const sourceBadge = sourceBadgeHtml(it);
    const categoryBadge = m.category
      ? `<span class="badge badge-cat" data-kind="${escapeHtml(categoryKind(m.category))}">${escapeHtml(m.category)}</span>`
      : '';

    const dailyBadge = typeof daily === 'number'
      ? `<span class="badge badge-daily ${daily > 0 ? 'up' : daily < 0 ? 'down' : ''}">${daily > 0 ? '+' : ''}${daily} ⭐ / 日</span>`
      : (it.source === 'github_trending'
        ? '<span class="badge badge-daily neutral">首次出现</span>'
        : '');

    li.innerHTML = `
      <div class="card-header">
        ${sourceBadge}
        ${categoryBadge}
      </div>
      <h3><a class="title-link" href="${url}">${escapeHtml(it.title || '(untitled)')}</a></h3>
      <p class="summary">${escapeHtml(it.summary || '')}</p>
      <div class="tag-list">${tags}</div>
      <div class="meta">
        <span class="badge badge-score ${scoreClass}">★ ${score.toFixed(1)}</span>
        ${dailyBadge}
        ${typeof stars === 'number' ? `<span>⭐ ${formatNumber(stars)}</span>` : ''}
        ${it.language && it.language !== 'unknown' ? `<span>${escapeHtml(it.language)}</span>` : ''}
        ${date ? `<span>${date}</span>` : ''}
      </div>
    `;
    return li;
  }

  function sourceBadgeHtml(it) {
    const m = it.metadata || {};
    if (it.source === 'github_trending') {
      return '<span class="badge badge-source source-github">GitHub</span>';
    }
    if (it.source === 'rss') {
      const feed = m.feed_name ? ' · ' + escapeHtml(m.feed_name) : '';
      return `<span class="badge badge-source source-rss">RSS${feed}</span>`;
    }
    return `<span class="badge badge-source source-other">${escapeHtml(it.source || '?')}</span>`;
  }

  function formatNumber(n) {
    n = Number(n);
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    return String(n);
  }

  function pad(n) { return String(n).padStart(2, '0'); }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }
})();
