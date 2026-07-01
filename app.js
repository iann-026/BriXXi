(() => {
  'use strict';

  const MONTHS_IT = [
    '', 'gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno',
    'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre',
  ];

  const els = {
    modeToggle: document.getElementById('modeToggle'),
    fortuneView: document.getElementById('fortuneView'),
    browseView: document.getElementById('browseView'),
    infoView: document.getElementById('infoView'),
    backToBrowse: document.getElementById('backToBrowse'),
    infoBtn: document.getElementById('infoBtn'),
    infoBack: document.getElementById('infoBack'),
    card: document.getElementById('card'),
    postDate: document.getElementById('postDate'),
    postText: document.getElementById('postText'),
    postLink: document.getElementById('postLink'),
    revealButton: document.getElementById('revealButton'),
    breadcrumb: document.getElementById('breadcrumb'),
    browseList: document.getElementById('browseList'),
    offlineNotice: document.getElementById('offlineNotice'),
  };

  let posts = [];
  let byYear = new Map(); // year -> Map(month -> [posts]), each list newest-first

  let lastIndex = -1;
  let browseReturnTarget = null;
  let browseState = { level: 'years' };
  let infoReturnBrowsing = false;

  function groupPosts() {
    byYear = new Map();
    for (let i = posts.length - 1; i >= 0; i--) {
      const post = posts[i];
      const d = new Date(post.date);
      const year = d.getFullYear();
      const month = d.getMonth() + 1;
      if (!byYear.has(year)) byYear.set(year, new Map());
      const monthMap = byYear.get(year);
      if (!monthMap.has(month)) monthMap.set(month, []);
      monthMap.get(month).push(post);
    }
  }

  function sortedYearsDesc() {
    return [...byYear.keys()].sort((a, b) => b - a);
  }

  function sortedMonthsDesc(year) {
    return [...byYear.get(year).keys()].sort((a, b) => b - a);
  }

  function renderPost(post) {
    els.postDate.textContent = post.display_date || '';
    els.postDate.href = post.source_url || '#';

    if (post.text && post.text.trim()) {
      els.postText.textContent = post.text;
      els.postText.classList.remove('empty');
    } else {
      els.postText.textContent = 'Un\u2019immagine, oggi.';
      els.postText.classList.add('empty');
    }

    if (post.links && post.links.length) {
      els.postLink.href = post.links[0];
      els.postLink.hidden = false;
    } else {
      els.postLink.removeAttribute('href');
      els.postLink.hidden = true;
    }
  }

  function pickRandomIndex() {
    if (posts.length <= 1) return 0;
    let i;
    do {
      i = Math.floor(Math.random() * posts.length);
    } while (i === lastIndex);
    return i;
  }

  function showRandomPost({ animate } = { animate: false }) {
    const i = pickRandomIndex();
    lastIndex = i;
    const post = posts[i];

    els.backToBrowse.hidden = true;
    browseReturnTarget = null;

    if (animate && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      els.card.classList.add('fading');
      setTimeout(() => {
        renderPost(post);
        els.card.classList.remove('fading');
      }, 220);
    } else {
      renderPost(post);
    }
  }

  function showChosenPost(post, returnTarget) {
    renderPost(post);
    browseReturnTarget = returnTarget;
    els.backToBrowse.hidden = false;
    setViewMode(false);
  }

  function formatPreview(post) {
    if (post.text && post.text.trim()) {
      const t = post.text.replace(/\s+/g, ' ').trim();
      return t.length > 60 ? t.slice(0, 60) + '\u2026' : t;
    }
    return 'Un\u2019immagine, oggi.';
  }

  function formatDayMonth(d) {
    const day = d.getDate();
    const month = MONTHS_IT[d.getMonth() + 1];
    return `${day} ${month}`;
  }

  function renderBreadcrumb() {
    els.breadcrumb.innerHTML = '';
    if (browseState.level === 'years') {
      const label = document.createElement('span');
      label.className = 'crumb-label';
      label.textContent = `${posts.length} pensieri in archivio \u2014 scegli un anno`;
      els.breadcrumb.appendChild(label);
      return;
    }

    const back = document.createElement('a');
    back.href = '#';
    back.className = 'crumb-back';

    if (browseState.level === 'months') {
      back.textContent = '\u2190 Tutti gli anni';
      back.addEventListener('click', (e) => { e.preventDefault(); goToYears(); });
      els.breadcrumb.appendChild(back);

      const label = document.createElement('span');
      label.className = 'crumb-label';
      label.textContent = String(browseState.year);
      els.breadcrumb.appendChild(label);
    } else if (browseState.level === 'posts') {
      back.textContent = `\u2190 ${browseState.year}`;
      back.addEventListener('click', (e) => { e.preventDefault(); goToMonths(browseState.year); });
      els.breadcrumb.appendChild(back);

      const label = document.createElement('span');
      label.className = 'crumb-label';
      const monthName = MONTHS_IT[browseState.month];
      label.textContent = `${monthName.charAt(0).toUpperCase()}${monthName.slice(1)} ${browseState.year}`;
      els.breadcrumb.appendChild(label);
    }
  }

  function clearBrowseList() {
    els.browseList.innerHTML = '';
  }

  function addRow(labelText, metaText, onClick) {
    const li = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'browse-row';

    const inner = document.createElement('div');
    inner.className = 'row-inner';

    const labelSpan = document.createElement('span');
    labelSpan.className = 'row-label';
    labelSpan.textContent = labelText;

    const metaSpan = document.createElement('span');
    metaSpan.className = 'row-meta';
    metaSpan.textContent = metaText;

    inner.appendChild(labelSpan);
    inner.appendChild(metaSpan);
    button.appendChild(inner);
    button.addEventListener('click', onClick);
    li.appendChild(button);
    els.browseList.appendChild(li);
  }

  function addSectionLabel(text) {
    const li = document.createElement('li');
    li.className = 'browse-section-label';
    li.textContent = text;
    els.browseList.appendChild(li);
  }

  function goToYears() {
    browseState = { level: 'years' };
    clearBrowseList();
    renderBreadcrumb();

    const latest = posts.slice(-3).reverse();
    if (latest.length) {
      addSectionLabel('Più recenti');
      for (const post of latest) {
        addRow(formatPreview(post), formatDayMonth(new Date(post.date)), () => {
          showChosenPost(post, { level: 'years' });
        });
      }
    }

    for (const year of sortedYearsDesc()) {
      let count = 0;
      for (const list of byYear.get(year).values()) count += list.length;
      addRow(String(year), `${count} pensieri`, () => goToMonths(year));
    }
  }

  function goToMonths(year) {
    browseState = { level: 'months', year };
    clearBrowseList();
    renderBreadcrumb();
    for (const month of sortedMonthsDesc(year)) {
      const list = byYear.get(year).get(month);
      const name = MONTHS_IT[month];
      const label = name.charAt(0).toUpperCase() + name.slice(1);
      addRow(label, `${list.length} pensieri`, () => goToPosts(year, month));
    }
  }

  function goToPosts(year, month) {
    browseState = { level: 'posts', year, month };
    clearBrowseList();
    renderBreadcrumb();
    const list = byYear.get(year).get(month);
    for (const post of list) {
      addRow(formatPreview(post), formatDayMonth(new Date(post.date)), () => {
        showChosenPost(post, { level: 'posts', year, month });
      });
    }
  }

  function renderBrowseAt(target) {
    if (!target || target.level === 'years') goToYears();
    else if (target.level === 'months') goToMonths(target.year);
    else goToPosts(target.year, target.month);
  }

  function setViewMode(browsing) {
    els.infoView.hidden = true;
    els.fortuneView.hidden = browsing;
    els.browseView.hidden = !browsing;
    els.modeToggle.setAttribute('aria-pressed', String(browsing));
    els.modeToggle.textContent = browsing ? 'Torna al pensiero del giorno' : "Sfoglia l'archivio";
    if (browsing) renderBrowseAt(browseState);
  }

  function showInfoView() {
    infoReturnBrowsing = !els.browseView.hidden;
    els.fortuneView.hidden = true;
    els.browseView.hidden = true;
    els.infoView.hidden = false;
  }

  els.modeToggle.addEventListener('click', () => {
    const browsing = els.browseView.hidden;
    setViewMode(browsing);
  });

  els.revealButton.addEventListener('click', () => showRandomPost({ animate: true }));

  els.backToBrowse.addEventListener('click', (e) => {
    e.preventDefault();
    if (browseReturnTarget) browseState = browseReturnTarget;
    setViewMode(true);
  });

  els.infoBtn.addEventListener('click', () => showInfoView());

  els.infoBack.addEventListener('click', (e) => {
    e.preventDefault();
    setViewMode(infoReturnBrowsing);
  });

  window.addEventListener('online', () => { els.offlineNotice.hidden = true; });
  window.addEventListener('offline', () => { els.offlineNotice.hidden = false; });
  if (!navigator.onLine) els.offlineNotice.hidden = false;

  fetch('posts.json')
    .then((r) => {
      if (!r.ok) throw new Error('network response not ok');
      return r.json();
    })
    .then((data) => {
      posts = data;
      groupPosts();
      showRandomPost({ animate: false });
    })
    .catch((err) => {
      els.postDate.textContent = '';
      els.postText.textContent = 'Non riesco a caricare i contenuti. Controlla la connessione e riprova.';
      els.postText.classList.add('empty');
      console.error('Failed to load posts.json', err);
    });

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('service-worker.js').catch((err) => {
        console.error('Service worker registration failed', err);
      });
    });
  }
})();
