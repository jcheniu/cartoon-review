import {rangeRows, rangeName} from './review.mjs';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function signature(rows) {
  const bytes = new TextEncoder().encode(JSON.stringify(rows));
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(b => b.toString(16).padStart(2, '0')).join('');
}
export async function createUploader({manifest, getState, onStatus}) {
  const response = await fetch(new URL('../upload-config.json', import.meta.url), {cache: 'no-store'});
  if (!response.ok) throw Error('上传配置暂时不可用');
  const config = await response.json();
  if (!config.endpoint || config.round_id !== manifest.round.id) throw Error('本轮上传服务尚未配置');
  let endpoint = new URL(config.endpoint);
  async function discover() {
    if (!config.discovery_url) return;
    try {
      const url = new URL(config.discovery_url);
      url.searchParams.set('t', String(Math.floor(Date.now() / 15000)));
      const response = await fetch(url, {cache: 'no-store', signal: AbortSignal.timeout(10000)});
      const next = await response.json();
      if (response.ok && next.round_id === manifest.round.id && new URL(next.endpoint).protocol === 'https:') endpoint = new URL(next.endpoint);
    } catch { /* Retain the last known endpoint and retry discovery later. */ }
  }
  await discover();
  if (endpoint.protocol !== 'https:') throw Error('上传服务必须使用 HTTPS');
  const session = getState().session;
  const keyName = 'cartoon-review-upload-key:' + session;
  let key = localStorage.getItem(keyName);
  if (!key) {
    key = [...crypto.getRandomValues(new Uint8Array(32))].map(b => b.toString(16).padStart(2, '0')).join('');
    localStorage.setItem(keyName, key);
  }
  const metaKey = 'cartoon-review-upload-state:' + manifest.round.id + ':' + session;
  let meta = JSON.parse(localStorage.getItem(metaKey) || '{"ranges":{},"uploaded":{}}');
  let running = false, retry = null;
  const persist = () => localStorage.setItem(metaKey, JSON.stringify(meta));
  const notify = (text, error = false, url = '') => onStatus({text, error, url});
  const request = async (path, body) => {
    let failure;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const result = await fetch(new URL(path, endpoint), {
          method: body ? 'POST' : 'GET',
          headers: {'X-Upload-Key': key, ...(body ? {'Content-Type': 'application/json'} : {})},
          body: body ? JSON.stringify(body) : undefined,
          signal: AbortSignal.timeout(20000),
        });
        const json = await result.json();
        if (!result.ok) throw Error(json.error || '上传服务暂时不可用');
        return json;
      } catch (error) { failure = error; await sleep(700 * (attempt + 1)); }
    }
    throw failure;
  };
  async function pump() {
    if (running) return;
    running = true;
    clearTimeout(retry);
    try {
      for (const [name, range] of Object.entries(meta.ranges)) {
        const rows = rangeRows(getState().reviews, range);
        if (!rows.length) continue;
        const currentSignature = await signature(rows);
        if (meta.uploaded[name] === currentSignature) continue;
        notify('正在上传 ' + name + '（' + rows.length + '/25）…');
        let result = await request('/api/reviews', {session_id: session, range, records: rows});
        for (let poll = 0; result.state !== 'uploaded'; poll++) {
          notify('已接收 ' + name + '，正在写入 GitHub…');
          await sleep(Math.min(3000 + poll * 500, 10000));
          result = await request('/api/status?session=' + session + '&start=' + range.start + '&end=' + range.end);
          if (poll >= 30) throw Error('GitHub 写入排队中，稍后自动重试');
        }
        meta.uploaded[name] = currentSignature;
        persist();
        notify('已上传 ' + name + '（' + result.count + '/25）', false, result.url);
      }
      // Another save may have occurred while this request was in flight.
      for (const [name, range] of Object.entries(meta.ranges)) {
        if (rangeRows(getState().reviews, range).length && meta.uploaded[name] !== await signature(rangeRows(getState().reviews, range))) {
          retry = setTimeout(pump, 300);
          break;
        }
      }
    } catch (error) {
      notify('已保存在本浏览器；上传未完成，稍后自动重试。' + (error.message || ''), true);
      discover().finally(() => { retry = setTimeout(pump, 15000); });
    } finally { running = false; }
  }
  function enqueue(range) {
    meta.ranges[rangeName(range)] = {...range};
    persist();
    pump();
  }
  for (const row of Object.values(getState().reviews)) {
    const start = Math.floor(row.image_number / 25) * 25;
    const range = {start, end: start + 24};
    meta.ranges[rangeName(range)] = range;
  }
  persist();
  window.addEventListener('online', pump);
  notify('自动上传已连接：保存后写入 GitHub result/round_1');
  pump();
  return {enqueue};
}
