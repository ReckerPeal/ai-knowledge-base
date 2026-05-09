(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);

  init();

  async function init() {
    const params = new URLSearchParams(location.search);
    const id = params.get('id');
    const date = params.get('date');
    if (!id) {
      $('status').textContent = '缺少文章 id 参数。';
      return;
    }

    try {
      const article = await locateArticle(id, date);
      if (!article) {
        $('status').textContent = `未找到 id=${id} 的文章。`;
        return;
      }
      render(article);
    } catch (err) {
      $('status').textContent = `加载失败：${err.message}`;
    }
  }

  async function locateArticle(id, date) {
    // Strategy: prefer the precise per-day file when date is known or
    // derivable from the id (id format: YYYYMMDD-...). Fall back to the
    // global index.json.
    const guessedDate = date || dateFromId(id);
    if (guessedDate) {
      const list = await fetchJSON(`data/by_date/${guessedDate}.json`).catch(() => null);
      if (Array.isArray(list)) {
        const hit = list.find((a) => a.id === id);
        if (hit) return hit;
      }
    }
    const all = await fetchJSON('data/index.json');
    return Array.isArray(all) ? all.find((a) => a.id === id) : null;
  }

  function dateFromId(id) {
    const m = /^(\d{4})(\d{2})(\d{2})-/.exec(id);
    return m ? `${m[1]}-${m[2]}-${m[3]}` : null;
  }

  async function fetchJSON(url) {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
    return res.json();
  }

  function render(a) {
    document.title = `${a.title} · AI 知识库`;
    $('status').hidden = true;
    $('article').hidden = false;

    $('title').textContent = a.title || '(untitled)';
    $('summary').textContent = a.summary || '';

    const score = Number(a.score || 0);
    const scoreClass = score >= 8 ? 'score-good' : score >= 6 ? 'score-mid' : 'score-low';
    const scoreEl = $('score-badge');
    scoreEl.textContent = `★ ${score.toFixed(1)}`;
    scoreEl.classList.add(scoreClass);

    if (a.source) $('source-badge').textContent = a.source;
    else $('source-badge').hidden = true;

    if (a.language) $('lang-badge').textContent = a.language;
    else $('lang-badge').hidden = true;

    const date = (a.collected_at || '').slice(0, 10);
    $('date').textContent = date ? `收集于 ${date}` : '';

    const tagsEl = $('tags');
    tagsEl.innerHTML = '';
    for (const t of a.tags || []) {
      const span = document.createElement('span');
      span.className = 'tag';
      span.textContent = t;
      tagsEl.appendChild(span);
    }

    const sourceLink = $('source-link');
    if (a.source_url) sourceLink.href = a.source_url;
    else sourceLink.hidden = true;

    renderMetadata(a);
    renderContent(a.content || '');
  }

  function renderMetadata(a) {
    const grid = $('metadata');
    const m = a.metadata || {};
    const items = [];
    if (m.author) items.push(['作者', m.author]);
    if (typeof m.stars === 'number') items.push(['Stars', formatNumber(m.stars)]);
    if (typeof m.daily_stars === 'number') {
      const sign = m.daily_stars > 0 ? '+' : '';
      const baseline = m.stars_baseline_date ? `（vs ${m.stars_baseline_date}）` : '';
      items.push(['当日新增', `${sign}${m.daily_stars}${baseline}`]);
    } else if (m.daily_stars === null) {
      items.push(['当日新增', '首次出现']);
    }
    if (typeof m.forks === 'number') items.push(['Forks', formatNumber(m.forks)]);
    if (typeof m.open_issues === 'number') items.push(['Issues', formatNumber(m.open_issues)]);
    if (a.published_at) items.push(['原发布', a.published_at.slice(0, 10)]);
    if (a.status) items.push(['状态', a.status]);

    if (items.length === 0) {
      grid.hidden = true;
      return;
    }
    grid.innerHTML = items.map(([k, v]) =>
      `<div class="item"><div class="k">${escapeHtml(k)}</div><div class="v">${escapeHtml(String(v))}</div></div>`
    ).join('');
  }

  function renderContent(text) {
    const target = $('content');
    if (!text) {
      target.innerHTML = '<p class="muted">暂无正文。</p>';
      return;
    }
    if (window.marked && window.DOMPurify) {
      const html = window.marked.parse(text, { breaks: true, gfm: true });
      target.innerHTML = window.DOMPurify.sanitize(html);
    } else {
      target.innerHTML = text.split(/\n{2,}/)
        .map((p) => `<p>${escapeHtml(p).replace(/\n/g, '<br>')}</p>`)
        .join('');
    }
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
