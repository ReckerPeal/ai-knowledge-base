(() => {
  'use strict';

  const DATA_URL = 'data/index.json';

  const $ = (id) => document.getElementById(id);

  init();

  async function init() {
    const id = new URLSearchParams(location.search).get('id');
    if (!id) {
      $('status').textContent = '缺少文章 id 参数。';
      return;
    }

    try {
      const res = await fetch(DATA_URL, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const all = await res.json();
      const article = all.find((a) => a.id === id);
      if (!article) {
        $('status').textContent = `未找到 id=${id} 的文章。`;
        return;
      }
      render(article);
    } catch (err) {
      $('status').textContent = `加载失败：${err.message}`;
    }
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
    if (m.stars != null) items.push(['Stars', formatNumber(m.stars)]);
    if (m.forks != null) items.push(['Forks', formatNumber(m.forks)]);
    if (m.open_issues != null) items.push(['Issues', formatNumber(m.open_issues)]);
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
