const $ = (s) => document.querySelector(s);

const imageEl = $('#image');
const messageEl = $('#message');
const frameEl = $('#frame');
const backBtn = $('#back');
const playBtn = $('#play');
const nextBtn = $('#next');
const edgeNextBtn = $('#edgeNext');
const loaderEl = $('#loader');

const overlay = $('#overlay');
const openControlsBtn = $('#openControls');
const closeControlsBtn = $('#closeControls');
const applyControlsBtn = $('#applyControls');
const themeToggleBtn = $('#themeToggle');

const yearFromEl = $('#yearFrom');
const yearToEl = $('#yearTo');
const yearsEl = $('#years');
const autoplaySecondsEl = $('#autoplaySeconds');

const REGION_IDS = [
  'north-america',
  'latin-america-caribbean',
  'africa',
  'south-asia',
  'east-asia',
  'mena',
  'europe',
  'global'
];

const FILTER_IDS = [
  'people',
  'signs',
  'fashion',
  'vehicles',
  'interiors',
  'objects',
  'religion',
  'animals',
  'architecture',
  'landscape',
  'flowers'
];

const DEFAULT_PREFS = {
  theme: 'light',
  yearFrom: 1994,
  yearTo: 2008,
  autoplaySeconds: 8,
  regions: Object.fromEntries(REGION_IDS.map((id) => [id, true])),
  filters: {
    people: true,
    signs: true,
    fashion: true,
    vehicles: true,
    interiors: true,
    objects: true,
    religion: true,
    animals: true,
    architecture: false,
    landscape: false,
    flowers: false
  }
};

const LOOKAHEAD = 3;
const MIN_SHORT_SIDE = 200;
const MIN_LONG_SIDE = 300;
const PERSISTENT_SEEN_KEY = 'shufflerSeenV25';
const PERSISTENT_SEEN_LIMIT = 3500;

let prefs = loadPrefs();
let manifest = null;
let loadedRegions = new Map();
let regionQueues = new Map();

let history = [];
let historyIndex = -1;

const seenSession = new Set();
const failedSession = new Set();
const reserved = new Set();

let persistentSeenList = loadPersistentSeenList();
let persistentSeenSet = new Set(persistentSeenList);

let preloadBuffer = [];
let fillPromise = null;
let generation = 0;

let autoplayRunning = false;
let autoplayTimer = null;
let chromeTimer = null;

function cloneDefaults() {
  return JSON.parse(JSON.stringify(DEFAULT_PREFS));
}

function clampYear(y) {
  return Math.max(1994, Math.min(2008, Number(y) || 1994));
}

function loadPrefs() {
  const result = cloneDefaults();
  try {
    const raw = JSON.parse(localStorage.getItem('shufflerPrefsV2') || '{}');
    result.theme = raw.theme === 'dark' ? 'dark' : 'light';
    if (Number.isFinite(+raw.yearFrom)) result.yearFrom = clampYear(+raw.yearFrom);
    if (Number.isFinite(+raw.yearTo)) result.yearTo = clampYear(+raw.yearTo);
    if ([5, 8, 10, 15, 20, 30].includes(+raw.autoplaySeconds)) {
      result.autoplaySeconds = +raw.autoplaySeconds;
    }
    for (const id of REGION_IDS) {
      if (raw.regions && typeof raw.regions[id] === 'boolean') result.regions[id] = raw.regions[id];
    }
    for (const id of FILTER_IDS) {
      if (raw.filters && typeof raw.filters[id] === 'boolean') result.filters[id] = raw.filters[id];
    }
  } catch {}
  return result;
}

function savePrefs() {
  try {
    localStorage.setItem('shufflerPrefsV2', JSON.stringify(prefs));
  } catch {}
}

function loadPersistentSeenList() {
  try {
    const raw = JSON.parse(localStorage.getItem(PERSISTENT_SEEN_KEY) || '[]');
    if (!Array.isArray(raw)) return [];
    return raw.filter((v) => typeof v === 'string').slice(-PERSISTENT_SEEN_LIMIT);
  } catch {
    return [];
  }
}

function savePersistentSeen() {
  try {
    localStorage.setItem(PERSISTENT_SEEN_KEY, JSON.stringify(persistentSeenList));
  } catch {}
}

function markSeenPersistent(key) {
  if (!key || persistentSeenSet.has(key)) return;
  persistentSeenSet.add(key);
  persistentSeenList.push(key);
  if (persistentSeenList.length > PERSISTENT_SEEN_LIMIT) {
    const overflow = persistentSeenList.length - PERSISTENT_SEEN_LIMIT;
    const removed = persistentSeenList.splice(0, overflow);
    for (const oldKey of removed) {
      if (!persistentSeenList.includes(oldKey)) persistentSeenSet.delete(oldKey);
    }
  }
  savePersistentSeen();
}

function applyTheme() {
  document.documentElement.dataset.theme = prefs.theme;
  themeToggleBtn.textContent = prefs.theme === 'light'
    ? 'Switch to Dark Mode'
    : 'Switch to Light Mode';
}

function updateYearLabel() {
  let a = clampYear(yearFromEl.value);
  let b = clampYear(yearToEl.value);
  if (a > b) [a, b] = [b, a];
  yearsEl.textContent = `${a} – ${b}`;
}

function syncControlsFromPrefs() {
  yearFromEl.value = prefs.yearFrom;
  yearToEl.value = prefs.yearTo;
  autoplaySecondsEl.value = String(prefs.autoplaySeconds);
  for (const id of REGION_IDS) {
    $(`#region-${id}`).checked = !!prefs.regions[id];
  }
  for (const id of FILTER_IDS) {
    $(`#filter-${id}`).checked = !!prefs.filters[id];
  }
  updateYearLabel();
}

function readControlsIntoPrefs() {
  let a = clampYear(yearFromEl.value);
  let b = clampYear(yearToEl.value);
  if (a > b) [a, b] = [b, a];

  prefs.yearFrom = a;
  prefs.yearTo = b;
  prefs.autoplaySeconds = +autoplaySecondsEl.value;

  for (const id of REGION_IDS) {
    prefs.regions[id] = $(`#region-${id}`).checked;
  }
  for (const id of FILTER_IDS) {
    prefs.filters[id] = $(`#filter-${id}`).checked;
  }
}

function setLoading() {
  imageEl.style.display = 'none';
  messageEl.style.display = 'none';
  loaderEl.style.display = 'flex';
}

function setMessage(text) {
  imageEl.style.display = 'none';
  loaderEl.style.display = 'none';
  messageEl.style.display = 'block';
  messageEl.textContent = text;
}

function itemKey(item) {
  return String(item.digest || item.visualKey || item.archive);
}

function itemAllowed(item) {
  const year = Number(item.year);
  if (year < prefs.yearFrom || year > prefs.yearTo) return false;

  const tags = new Set(Array.isArray(item.tags) ? item.tags : []);
  for (const id of FILTER_IDS) {
    if (!prefs.filters[id] && tags.has(id)) return false;
  }
  return true;
}

function selectedRegions() {
  return REGION_IDS.filter((id) => prefs.regions[id]);
}

async function loadManifest() {
  const response = await fetch(`./catalog/manifest.json?v=${Date.now()}`, {
    cache: 'no-store',
    credentials: 'same-origin',
    redirect: 'error'
  });
  if (!response.ok) throw new Error('manifest');
  const data = await response.json();
  if (!data || !data.regions) throw new Error('manifest');
  manifest = data;
}

async function loadRegion(region) {
  if (loadedRegions.has(region)) return loadedRegions.get(region);

  const info = manifest.regions[region];
  if (!info || !info.file) {
    loadedRegions.set(region, []);
    return [];
  }

  const response = await fetch(`./${info.file}?v=${manifest.version || '1'}`, {
    cache: 'force-cache',
    credentials: 'same-origin',
    redirect: 'error'
  });

  if (!response.ok) {
    loadedRegions.set(region, []);
    return [];
  }

  const data = await response.json();
  const items = Array.isArray(data) ? data : [];
  loadedRegions.set(region, items);
  return items;
}

async function ensureSelectedRegionsLoaded() {
  await Promise.all(selectedRegions().map(loadRegion));
}

function shuffle(items) {
  const a = items.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function rebuildQueues() {
  generation += 1;
  clearPreloadBuffer();
  regionQueues = new Map();

  for (const region of selectedRegions()) {
    const all = loadedRegions.get(region) || [];
    const eligible = all.filter((item) => {
      const key = itemKey(item);
      return itemAllowed(item) &&
        !seenSession.has(key) &&
        !persistentSeenSet.has(key) &&
        !failedSession.has(item.archive);
    });
    regionQueues.set(region, shuffle(eligible));
  }
}

function clearPreloadBuffer() {
  preloadBuffer = [];
  reserved.clear();
  fillPromise = null;
}

function popBalancedCandidate() {
  const available = selectedRegions().filter((region) => {
    const queue = regionQueues.get(region);
    return queue && queue.length;
  });
  if (!available.length) return null;

  const region = available[Math.floor(Math.random() * available.length)];
  const queue = regionQueues.get(region);

  while (queue.length) {
    const item = queue.pop();
    const key = itemKey(item);

    if (seenSession.has(key)) continue;
    if (persistentSeenSet.has(key)) continue;
    if (reserved.has(key)) continue;
    if (failedSession.has(item.archive)) continue;

    reserved.add(key);
    return item;
  }

  return popBalancedCandidate();
}

function preloadImage(item, token, timeoutMs = 12000) {
  return new Promise((resolve, reject) => {
    const test = new Image();
    let settled = false;

    const finish = (ok, reason) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      test.onload = null;
      test.onerror = null;
      if (ok) {
        resolve({
          item,
          url: test.src,
          key: itemKey(item),
          width: test.naturalWidth,
          height: test.naturalHeight
        });
      } else {
        reject(new Error(reason || 'image failed'));
      }
    };

    const timer = setTimeout(() => finish(false, 'timeout'), timeoutMs);

    test.onload = async () => {
      if (token !== generation) return finish(false, 'stale');

      const shortSide = Math.min(test.naturalWidth, test.naturalHeight);
      const longSide = Math.max(test.naturalWidth, test.naturalHeight);
      if (shortSide < MIN_SHORT_SIDE || longSide < MIN_LONG_SIDE) {
        return finish(false, 'too small');
      }

      try {
        if (typeof test.decode === 'function') await test.decode();
      } catch {}

      finish(true);
    };

    test.onerror = () => finish(false, 'image failed');
    test.referrerPolicy = 'no-referrer';
    test.src = item.archive;
  });
}

async function fillBuffer() {
  if (fillPromise) return fillPromise;

  const token = generation;

  fillPromise = (async () => {
    while (preloadBuffer.length < LOOKAHEAD && token === generation) {
      const item = popBalancedCandidate();
      if (!item) break;

      try {
        const entry = await preloadImage(item, token);
        if (token !== generation) break;
        preloadBuffer.push(entry);
      } catch (error) {
        const key = itemKey(item);
        reserved.delete(key);
        if (error.message !== 'stale') failedSession.add(item.archive);
      }
    }
  })().finally(() => {
    fillPromise = null;
  });

  return fillPromise;
}

async function takePreparedEntry() {
  if (!preloadBuffer.length) await fillBuffer();
  const entry = preloadBuffer.shift() || null;
  if (entry) {
    reserved.delete(entry.key);
    seenSession.add(entry.key);
    markSeenPersistent(entry.key);
  }
  fillBuffer();
  return entry;
}

function render(entry) {
  if (!entry) return;
  loaderEl.style.display = 'none';
  messageEl.style.display = 'none';
  imageEl.style.display = 'block';
  imageEl.src = entry.url;
  backBtn.disabled = historyIndex <= 0;
  scheduleAutoplay();
}

async function advance() {
  clearAutoplayTimer();

  if (historyIndex < history.length - 1) {
    historyIndex += 1;
    render(history[historyIndex]);
    return;
  }

  nextBtn.disabled = true;
  setLoading();

  try {
    const entry = await takePreparedEntry();
    if (!entry) {
      setMessage('No unseen images left for the current controls right now.');
      pauseAutoplay(true);
      return;
    }

    history.push(entry);
    historyIndex = history.length - 1;
    render(entry);
  } finally {
    nextBtn.disabled = false;
    backBtn.disabled = historyIndex <= 0;
  }
}

function goBack() {
  clearAutoplayTimer();
  if (historyIndex > 0) {
    historyIndex -= 1;
    render(history[historyIndex]);
  }
}

function clearAutoplayTimer() {
  if (autoplayTimer) {
    clearTimeout(autoplayTimer);
    autoplayTimer = null;
  }
}

function clearChromeTimer() {
  if (chromeTimer) {
    clearTimeout(chromeTimer);
    chromeTimer = null;
  }
}

function scheduleAutoplay() {
  clearAutoplayTimer();
  clearChromeTimer();

  if (!autoplayRunning || overlay.classList.contains('open')) return;

  chromeTimer = setTimeout(() => {
    if (autoplayRunning && !overlay.classList.contains('open')) {
      document.body.classList.add('autoplay-running');
    }
  }, 1400);

  autoplayTimer = setTimeout(() => {
    if (autoplayRunning && !overlay.classList.contains('open')) {
      advance();
    }
  }, prefs.autoplaySeconds * 1000);
}

function playAutoplay() {
  if (!history.length) return;
  autoplayRunning = true;
  playBtn.classList.add('is-playing');
  playBtn.setAttribute('aria-label', 'Pause auto-play');
  scheduleAutoplay();
}

function pauseAutoplay(showChrome = true) {
  autoplayRunning = false;
  clearAutoplayTimer();
  clearChromeTimer();
  playBtn.classList.remove('is-playing');
  playBtn.setAttribute('aria-label', 'Play auto-play');
  if (showChrome) document.body.classList.remove('autoplay-running');
}

function toggleAutoplay() {
  if (autoplayRunning) pauseAutoplay(true);
  else playAutoplay();
}

function pauseAndRevealCurrentImage() {
  if (!autoplayRunning) return;
  pauseAutoplay(true);
}

async function applyControls() {
  readControlsIntoPrefs();
  savePrefs();
  overlay.classList.remove('open');

  if (historyIndex < history.length - 1) {
    history = history.slice(0, historyIndex + 1);
  }

  await ensureSelectedRegionsLoaded();
  rebuildQueues();
  fillBuffer();

  if (!history.length) await advance();
  else render(history[historyIndex]);
}

function shouldIgnoreKey(event) {
  if (event.defaultPrevented) return true;
  if (event.altKey || event.ctrlKey || event.metaKey) return true;
  if (overlay.classList.contains('open')) return true;
  const t = event.target;
  const tag = (t && t.tagName ? t.tagName : '').toUpperCase();
  return tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || (t && t.isContentEditable);
}

async function handleKeydown(event) {
  if (shouldIgnoreKey(event)) return;

  if (event.key === 'ArrowLeft') {
    event.preventDefault();
    pauseAutoplay(true);
    goBack();
  } else if (event.key === 'ArrowRight') {
    event.preventDefault();
    pauseAutoplay(true);
    await advance();
  }
}

backBtn.addEventListener('click', () => {
  pauseAutoplay(true);
  goBack();
});

nextBtn.addEventListener('click', async () => {
  pauseAutoplay(true);
  await advance();
});

edgeNextBtn.addEventListener('click', async () => {
  pauseAutoplay(true);
  await advance();
});

playBtn.addEventListener('click', toggleAutoplay);
frameEl.addEventListener('click', pauseAndRevealCurrentImage);
window.addEventListener('keydown', handleKeydown);

openControlsBtn.addEventListener('click', () => {
  pauseAutoplay(true);
  syncControlsFromPrefs();
  overlay.classList.add('open');
});

closeControlsBtn.addEventListener('click', () => {
  overlay.classList.remove('open');
});

applyControlsBtn.addEventListener('click', applyControls);

overlay.addEventListener('click', (event) => {
  if (event.target === overlay) overlay.classList.remove('open');
});

themeToggleBtn.addEventListener('click', () => {
  prefs.theme = prefs.theme === 'light' ? 'dark' : 'light';
  applyTheme();
  savePrefs();
});

yearFromEl.addEventListener('input', updateYearLabel);
yearToEl.addEventListener('input', updateYearLabel);

async function boot() {
  applyTheme();
  syncControlsFromPrefs();
  setLoading();

  try {
    await loadManifest();
    await ensureSelectedRegionsLoaded();
    rebuildQueues();
    await fillBuffer();
    await advance();
  } catch {
    setMessage('Catalog is not ready yet.');
  }
}

boot();
