import {rangeRows, rangeName} from './review.mjs';

class Superseded extends Error {}
function pause(ms, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new Superseded()); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, ms);
    if (signal?.aborted) abort();
    else signal?.addEventListener('abort', abort, {once: true});
  });
}
async function signature(rows) {
  const bytes = new TextEncoder().encode(JSON.stringify(rows.map(({review_version, reviewed_at, ...annotation}) => annotation)));
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(b => b.toString(16).padStart(2, '0')).join('');
}
export async function createUploader({manifest, getState, onStatus}) {
  const response = await fetch(new URL('../upload-config.json', import.meta.url), {cache: 'no-store'});
  if (!response.ok) throw Error('上传配置暂时不可用');
  const config = await response.json();
  if (!config.endpoint || config.round_id !== manifest.round.id) throw Error('本轮上传服务尚未配置');
  const endpoint = new URL(config.endpoint);
  if (endpoint.protocol !== 'https:') throw Error('上传服务必须使用 HTTPS');
  const session = getState().session;
  const keyName = 'cartoon-review-upload-key:' + session;
  let key = localStorage.getItem(keyName);
  if (!key) {
    key = [...crypto.getRandomValues(new Uint8Array(32))].map(b => b.toString(16).padStart(2, '0')).join('');
    localStorage.setItem(keyName, key);
  }
  const metaKey = 'cartoon-review-upload-state:' + manifest.round.id + ':' + session;
  const meta = JSON.parse(localStorage.getItem(metaKey) || '{"ranges":{},"uploaded":{}}');
  meta.saved ||= {};
  meta.pending ||= {};
  let running = false, retry = null, active = null;
  const persist = () => localStorage.setItem(metaKey, JSON.stringify(meta));
  const notify = (text, error = false, url = '') => onStatus({text, error, url});
  async function request(path, body, signal) {
    let failure;
    for (let attempt = 0; attempt < 3; attempt++) {
      if (signal.aborted) throw new Superseded();
      const controller = new AbortController();
      const abort = () => controller.abort();
      signal.addEventListener('abort', abort, {once: true});
      const timer = setTimeout(abort, 20000);
      try {
        const result = await fetch(new URL(path, endpoint), {
          method: body ? 'POST' : 'GET',
          headers: {'X-Upload-Key': key, ...(body ? {'Content-Type': 'application/json'} : {})},
          body: body ? JSON.stringify(body) : undefined,
          signal: controller.signal,
        });
        const json = await result.json();
        if (signal.aborted) throw new Superseded();
        if (!result.ok) throw Error(json.error || '上传服务暂时不可用');
        return json;
      } catch (error) {
        if (signal.aborted) throw new Superseded();
        failure = error;
      } finally {
        clearTimeout(timer);
        signal.removeEventListener('abort', abort);
      }
      if (attempt < 2) await pause(700 * (attempt + 1), signal);
    }
    throw failure;
  }
  async function queue() {
    const entries = [];
    for (const [name, range] of Object.entries(meta.ranges)) {
      const rows = rangeRows(getState().reviews, range);
      if (!rows.length) continue;
      const digest = await signature(rows);
      const send = Boolean(meta.pending[name]) || meta.saved[name] !== digest;
      if (send || (rows.length === 25 && meta.uploaded[name] !== digest)) {
        // Submit every new snapshot before polling GitHub publication.
        entries.push({name, range, priority: send ? (rows.length === 25 ? 0 : 1) : 2});
      }
    }
    return entries.sort((a, b) => a.priority - b.priority);
  }
  async function pump() {
    if (running) return;
    running = true;
    clearTimeout(retry);
    let delay = null;
    try {
      for (const {name, range} of await queue()) {
        const rows = rangeRows(getState().reviews, range);
        const currentSignature = await signature(rows);
        const send = Boolean(meta.pending[name]) || meta.saved[name] !== currentSignature;
        active = {name, complete: rows.length === 25, send, controller: new AbortController()};
        let result;
        if (send) {
          meta.pending[name] = currentSignature;
          persist(); // A lost acknowledgement may still have changed the receiver's queued snapshot.
          notify('正在保存 ' + name + '（' + rows.length + '/25）…');
          result = await request('/api/reviews', {session_id: session, range, records: rows}, active.controller.signal);
          meta.saved[name] = currentSignature;
          delete meta.pending[name];
          persist();
        } else {
          result = await request('/api/status?session=' + session + '&start=' + range.start + '&end=' + range.end,
            undefined, active.controller.signal);
        }
        active = null;
        if (rows.length < 25) {
          delete meta.uploaded[name];
          persist();
          notify('已保存 ' + name + '（' + rows.length + '/25）；满 25 张后自动提交');
        } else if (result.state === 'uploaded' && result.count === 25) {
          meta.uploaded[name] = currentSignature;
          persist();
          notify('已上传 ' + name + '（25/25）', false, result.url);
        } else {
          // A pending response overrides an older cached publication, including a revert.
          delete meta.uploaded[name];
          persist();
          const waiting = result.ready_at > Date.now() / 1000;
          notify(name + ' JSONL 已保存；' + (result.error || (waiting
            ? '合并连续修改后自动提交 GitHub…' : '正在提交 GitHub…')));
        }
      }
      const remaining = await queue();
      if (remaining.length) delay = remaining.some(entry => entry.priority < 2) ? 0 : 2000;
    } catch (error) {
      if (error instanceof Superseded) delay = 0;
      else {
        notify('已保存在本浏览器；上传未完成，稍后自动重试。' + (error.message || ''), true);
        delay = 3000;
      }
    } finally {
      active = null;
      running = false;
      if (delay !== null) { clearTimeout(retry); retry = setTimeout(pump, delay); }
    }
  }
  function enqueue(range) {
    const name = rangeName(range);
    meta.ranges[name] = {...range};
    persist();
    const complete = rangeRows(getState().reviews, range).length === 25;
    // Never keep retrying an obsolete draft while a newer complete group waits.
    if (active && (active.name === name || (complete && (!active.complete || !active.send)))) active.controller.abort();
    clearTimeout(retry);
    retry = setTimeout(pump, complete ? 0 : 500);
  }
  for (const row of Object.values(getState().reviews)) {
    const start = Math.floor(row.image_number / 25) * 25;
    meta.ranges[rangeName({start, end: start + 24})] = {start, end: start + 24};
  }
  persist();
  window.addEventListener('online', pump);
  notify('自动上传已连接：满 25 张、JSONL 保存后立即提交 GitHub');
  pump();
  return {enqueue};
}
