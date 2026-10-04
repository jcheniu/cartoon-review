import {createUploader} from './upload.mjs?v=20261005-four-rounds';
import {parseRange, shiftRange} from './ranges.mjs?v=20261005-four-rounds';
import {pad, rangeName, choiceOf, makeReview, validateImported, rangeRows} from './review.mjs?v=20261005-four-rounds';

const $ = selector => document.querySelector(selector);
const choices = [...document.querySelectorAll('[data-choice]')];
const reasonInputs = [...document.querySelectorAll('[name="rejection-reason"]')];
let storedSnapshot = null, uploader = null;
let manifest, activeRound, viewItems, total, storageKey, state, range = {start: 0, end: 24}, visible = [], index = 0, choice = null;

function message(text, error = false) {
  $('#message').textContent = text;
  $('#message').classList.toggle('error', error);
}
function persist(next) {
  if (localStorage.getItem(storageKey) !== storedSnapshot) throw Error('另一个标签页已修改标注，请刷新后继续。');
  const serialized = JSON.stringify(next);
  try { localStorage.setItem(storageKey, serialized); }
  catch { throw Error('浏览器保存失败。请允许本站本地存储或清理空间，并立即导出已标注数据。'); }
  storedSnapshot = serialized;
  state = next;
}
function current() { return visible[index]; }
function hasValidReview(item) {
  const review = state.reviews[item.id];
  return review && item.candidates && review.source_sha256 === item.sha256 &&
    review.candidate_hashes?.a === item.candidates.a.sha256 &&
    review.candidate_hashes?.b === item.candidates.b.sha256;
}
function imagesReady() {
  return ['#source', '#a', '#b'].every(selector => $(selector).getAttribute('src') && $(selector).complete && $(selector).naturalWidth > 0);
}
function updateChoices() {
  const ready = Boolean(current()?.candidates);
  for (const button of choices) {
    button.disabled = !ready;
    button.classList.toggle('active', button.dataset.choice === choice);
    button.setAttribute('aria-pressed', String(button.dataset.choice === choice));
  }
  $('#prefer-label').hidden = choice !== 'both';
  $('#rejection-reasons').hidden = choice !== 'neither';
  $('#save').disabled = !ready || !choice || !imagesReady();
  for (const variant of ['a', 'b']) {
    $('#card-' + variant).classList.toggle('selected', choice === variant || choice === 'both');
  }
}
function choose(value) {
  if (!current()?.candidates) return;
  choice = value;
  updateChoices();
}
function image(selector, path) {
  const img = $(selector);
  img.onload = updateChoices;
  img.onerror = () => { updateChoices(); message('图片加载失败，请刷新页面后再标注。', true); };
  if (path) img.src = new URL(path, document.baseURI).href;
  else img.removeAttribute('src');
}
function renderItem() {
  const item = current();
  image('#source', item?.path);
  image('#a', item?.candidates?.a.path);
  image('#b', item?.candidates?.b.path);
  $('#prev').disabled = index <= 0;
  $('#next').disabled = index >= visible.length - 1;
  $('#prev-group').disabled = range.start === 0;
  $('#next-group').disabled = range.end === total - 1;
  const review = item && hasValidReview(item) ? state.reviews[item.id] : null;
  choice = choiceOf(review);
  $('#caption').value = review?.caption ?? item?.caption ?? '';
  $('#preferred').value = review?.preferred === 'b' ? 'b' : review?.preferred === 'tie' ? 'tie' : 'a';
  reasonInputs.forEach(input => { input.checked = (review?.rejection_reasons || []).includes(input.value); });
  $('#caption').disabled = !item?.candidates;
  $('#badge').textContent = item ? (item.category === 'animal' ? (item.media_type === 'illustration' ? '宠物插画' : '宠物照片') : '历史动漫脸') : '本组';
  $('#item-title').textContent = item ? pad(item.image_number) + (item.candidates ? '' : ' · 候选待生成') : '当前筛选下没有图片';
  $('#position').textContent = item ? (index + 1) + ' / ' + visible.length : '可切换“全部”或下一组';
  $('#dimensions').textContent = item ? item.width + '×' + item.height : '';
  for (const variant of ['a', 'b']) {
    $('#strength-' + variant).textContent = item?.candidates ? '256×256 · 强度 ' + item.candidates[variant].strength : '待生成';
  }
  $('#save-state').textContent = review ? '已保存在本浏览器 · 版本 ' + review.review_version : item?.candidates ? '选择合格情况后保存到本浏览器' : '等待维护者发布此图的候选';
  $('#provenance').textContent = item ? item.attribution + ' · ' + item.license + ' · 源编号 ' + item.source_id : '';
  if (item) $('#source-link').href = item.source_page;
  else $('#source-link').removeAttribute('href');
  updateChoices();
}
function refresh(preferredId) {
  const members = viewItems.filter(item => item.image_number >= range.start && item.image_number <= range.end);
  visible = members.filter(item =>
    (!$('#category').value || item.category === $('#category').value) &&
    ($('#filter').value === 'all' || ($('#filter').value === 'reviewed' ? hasValidReview(item) : !hasValidReview(item))));
  index = Math.max(0, visible.findIndex(item => item.id === preferredId));
  const done = members.filter(hasValidReview).length;
  $('#progress').textContent = viewItems.filter(hasValidReview).length + ' / ' + total + ' 已标注';
  $('#dataset').textContent = viewItems.filter(item => item.candidates).length + ' / ' + total + ' 张候选就绪';
  $('#batch-progress').textContent = pad(range.start) + '–' + pad(range.end) + ' · 已标 ' + done + '/25 · 候选 ' + members.filter(x => x.candidates).length + '/25';
  $('#snapshot').textContent = activeRound.label + '；生成轮次 ' + manifest.round.id + '；发布快照 ' + new Date(manifest.generated_at).toLocaleString() + '。';
  renderItem();
}
function applyRange(value) {
  try {
    range = parseRange(value, total);
    $('#range').value = range.start + '-' + range.end;
    const url = new URL(location.href);
    url.searchParams.set('range', range.start + '-' + range.end);
    history.replaceState(null, '', url);
    refresh();
    message('');
  } catch (error) { message(error.message, true); }
}
function download(rows, name) {
  if (!rows.length) throw Error('当前范围还没有已保存的标注。');
  const blob = new Blob([rows.map(row => JSON.stringify(row)).join('\n') + '\n'], {type: 'application/x-ndjson;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
function save() {
  try {
    const item = current();
    if (!item) return;
    if (!imagesReady()) throw Error('请等待三张图片加载完成后保存。');
    const following = visible[index + 1]?.id;
    const review = makeReview(manifest, item, {
      choice, preferred: $('#preferred').value,
      reasons: reasonInputs.filter(input => input.checked).map(input => input.value),
      caption: $('#caption').value,
    }, state.reviews[item.id], state.session);
    if (review === state.reviews[item.id]) {
      refresh(following);
      message(pad(item.image_number) + ' 内容未变化，已保留原标注。');
      return;
    }
    persist({...state, reviews: {...state.reviews, [item.id]: review}});
    try { uploader?.enqueue(range); } catch { $('#upload-state').textContent = '本地保存成功；上传队列不可写，请导出本组 JSONL。'; }
    const rows = rangeRows(state.reviews, range);
    let exported = '';
    if (rows.length === 25) {
      download(rows, rangeName(range));
      exported = ' 本组 25 张已完成，已发起下载 ' + rangeName(range) + '。若未收到，请点击导出本组。';
    }
    refresh(following);
    message(pad(item.image_number) + ' 已保存。' + exported);
  } catch (error) { message(error.message, true); }
}
async function importFile(event) {
  try {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) throw Error('文件超过 10 MB，请导入本工具导出的 JSONL。');
    const lines = (await file.text()).split(/\r?\n/).filter(line => line.trim());
    if (!lines.length || lines.length > 500) throw Error('文件需包含 1–500 条标注。');
    const rows = lines.map(line => validateImported(manifest, JSON.parse(line)));
    if (new Set(rows.map(row => row.photo_id)).size !== rows.length) throw Error('文件中有重复的图片编号。');
    const merged = {...state.reviews};
    for (const row of rows) merged[row.photo_id] = row;
    persist({...state, reviews: merged});
    for (const row of rows) { const start = Math.floor(row.image_number / 25) * 25; uploader?.enqueue({start, end: start + 24}); }
    refresh();
    message('已导入并保存 ' + rows.length + ' 条标注。');
  } catch (error) { message('导入失败，原标注未更改：' + error.message, true); }
  finally { event.target.value = ''; }
}

async function start() {
  const params = new URL(location.href).searchParams;
  const catalogResponse = await fetch(new URL('../data/rounds.json', import.meta.url), {cache: 'no-cache'});
  if (!catalogResponse.ok) throw Error('轮次目录读取失败：' + catalogResponse.status);
  const catalog = await catalogResponse.json();
  activeRound = catalog.rounds.find(round => round.id === (params.get('round') || 'round_1'));
  if (!activeRound) throw Error('标注链接中的 round 不存在。');
  for (const round of catalog.rounds) {
    const option = document.createElement('option');
    option.value = round.id;
    option.textContent = round.label + ' · ' + round.ready + '/' + round.count + ' 对就绪';
    $('#round').append(option);
  }
  $('#round').value = activeRound.id;
  $('#round').addEventListener('change', () => {
    const next = new URL(location.href);
    next.searchParams.set('round', $('#round').value);
    next.searchParams.set('range', '0-24');
    next.searchParams.delete('archive');
    location.assign(next);
  });
  const response = await fetch(new URL(activeRound.manifest, document.baseURI), {cache: 'no-cache'});
  if (!response.ok) throw Error('数据清单读取失败：' + response.status);
  manifest = await response.json();
  if (manifest.round.id !== activeRound.round_id) throw Error('轮次目录与数据清单不匹配，请刷新。');
  const archive = activeRound.id === 'round_1' && params.get('archive') === '1';
  viewItems = archive ? manifest.items : manifest.items.slice(0, activeRound.count);
  total = viewItems.length;
  $('#category').parentElement.hidden = !archive;
  $('#range').placeholder = '0-24 或 ' + (total-25) + '-' + (total-1);
  $('#round-destination').textContent = 'result/' + activeRound.id;
  $('#round-summary').textContent = activeRound.label + '，共 ' + total + ' 张。各 round 的标注、草稿与上传目录互相独立。';
  storageKey = 'cartoon-review:' + manifest.dataset_sha256 + ':' + manifest.round.id;
  const raw = localStorage.getItem(storageKey);
  storedSnapshot = raw;
  if (raw) {
    state = JSON.parse(raw);
    if (!state || typeof state.session !== 'string' || !state.reviews || typeof state.reviews !== 'object') throw Error('本地标注格式异常，请先备份浏览器存储，勿直接清除。');
    const cleaned = {};
    for (const row of Object.values(state.reviews)) {
      const validated = validateImported(manifest, row);
      cleaned[validated.photo_id] = validated;
    }
    state.reviews = cleaned;
  } else {
    state = {session: crypto.randomUUID(), reviews: {}};
    persist(state);
  }
  const queryRange = new URL(location.href).searchParams.get('range');
  if (queryRange) {
    try { range = parseRange(queryRange, total); $('#range').value = range.start + '-' + range.end; }
    catch { message('链接中的范围无效，已使用 0-24。', true); }
  }
  for (const id of ['category', 'filter']) $('#' + id).addEventListener('change', () => refresh());
  choices.forEach(button => button.addEventListener('click', () => choose(button.dataset.choice)));
  $('#save').addEventListener('click', save);
  $('#prev').addEventListener('click', () => { if (index > 0) { index--; renderItem(); } });
  $('#next').addEventListener('click', () => { if (index < visible.length - 1) { index++; renderItem(); } });
  $('#apply-range').addEventListener('click', () => applyRange($('#range').value));
  $('#range').addEventListener('keydown', event => { if (event.key === 'Enter') applyRange($('#range').value); });
  for (const [id, delta] of [['prev-group', -1], ['next-group', 1]]) {
    $('#' + id).addEventListener('click', () => { const next = shiftRange(range, delta, total); applyRange(next.start + '-' + next.end); });
  }
  $('#export-now').addEventListener('click', () => {
    try { download(rangeRows(state.reviews, range), rangeName(range)); message('已发起本组 JSONL 下载。'); }
    catch (error) { message(error.message, true); }
  });
  $('#import').addEventListener('change', importFile);
  document.addEventListener('keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(event.target.tagName)) return;
    if (['1', '2', '3', '4'].includes(event.key)) { event.preventDefault(); choose(['a', 'b', 'both', 'neither'][Number(event.key) - 1]); }
    if (event.key === 'Enter' && !$('#save').disabled) { event.preventDefault(); save(); }
  });
  window.addEventListener('storage', event => {
    if (event.key === storageKey) {
      message('另一个标签页修改了标注。请刷新当前页面后继续，以免覆盖新记录。', true);
      $('#save').disabled = true;
      $('#save').onclick = event => event.stopImmediatePropagation();
      location.reload();
    }
  });
  refresh();
  const connectUploads = async () => {
    try {
      uploader = await createUploader({manifest, getState: () => state, onStatus: ({text, error, url}) => {
        $('#upload-state').textContent = text;
        $('#upload-state').classList.toggle('error', error);
        $('#upload-link').hidden = !url;
        if (url) $('#upload-link').href = url;
      }});
    } catch {
      $('#upload-state').textContent = '上传服务连接中断，30 秒后重试；本地保存和 JSONL 导出仍可用。';
      setTimeout(connectUploads, 30000);
    }
  };
  connectUploads();
}
start().catch(error => {
  message(error.message, true);
  $('#progress').textContent = '加载失败';
  for (const button of document.querySelectorAll('button')) button.disabled = true;
});
