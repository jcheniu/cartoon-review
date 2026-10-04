export const pad = value => String(value).padStart(3, '0');
export const rangeName = range => pad(range.start) + '_' + pad(range.end) + '.jsonl';

export function choiceOf(review) {
  if (!review) return null;
  return review.accepted;
}

export function makeReview(manifest, item, form, previous, session) {
  if (!item.candidates) throw Error('此图的候选尚未生成，暂时不能标注。');
  if (!['a', 'b', 'both', 'neither'].includes(form.choice)) throw Error('请先选择合格情况。');
  const reasons = form.choice === 'neither' ? [...new Set(form.reasons || [])] : [];
  if (form.choice === 'neither' && (!reasons.length || reasons.some(r => !['color', 'pose'].includes(r)))) {
    throw Error('两张都不合格时，请至少选择一个原因：颜色或姿态。');
  }
  const accepted = form.choice;
  const preferred = form.choice === 'both' ? form.preferred : accepted;
  if (accepted === 'both' && !['a', 'b', 'tie'].includes(preferred)) throw Error('请选择更偏好的候选。');
  if (accepted !== 'neither' && String(form.caption || '').trim().length < 5) throw Error('请用英语描述合格的目标图，至少 5 个字符。');
  const caption = String(form.caption || '').trim().slice(0, 2000);
  if (previous && previous.accepted === accepted && previous.preferred === preferred &&
      previous.caption === caption &&
      JSON.stringify([...previous.rejection_reasons].sort()) === JSON.stringify([...reasons].sort())) return previous;
  return {
    schema_version: 1,
    dataset_sha256: manifest.dataset_sha256,
    numbered_manifest_sha256: manifest.numbered_manifest_sha256,
    round_id: manifest.round.id,
    annotation_session_id: session,
    item_id: item.legacy_id,
    photo_id: item.id,
    image_number: item.image_number,
    category: item.category,
    split: item.split,
    group_id: item.group_id,
    review_version: (previous?.review_version || 0) + 1,
    reviewed_at: new Date().toISOString(),
    accepted, preferred, rejection_reasons: reasons,
    caption,
    source_sha256: item.sha256,
    source: {path: item.path, sha256: item.preview_sha256 || item.sha256, ...(item.preview_sha256 ? {original_sha256: item.sha256} : {}), license: item.license, source_url: item.source_url},
    candidate_hashes: {a: item.candidates.a.sha256, b: item.candidates.b.sha256},
    master_hashes: item.master_hashes,
    candidates: item.candidates,
  };
}

export function validateImported(manifest, row) {
  const item = manifest.items.find(item => item.id === row.photo_id);
  if (!item || !item.candidates || row.dataset_sha256 !== manifest.dataset_sha256 ||
      row.round_id !== manifest.round.id || row.source_sha256 !== item.sha256 ||
      row.candidate_hashes?.a !== item.candidates.a.sha256 || row.candidate_hashes?.b !== item.candidates.b.sha256) {
    throw Error('导入文件的数据集、轮次或图片校验值不匹配。');
  }
  if (!['a', 'b', 'both', 'neither'].includes(row.accepted) ||
      !Array.isArray(row.rejection_reasons) || (row.accepted !== 'neither' && row.rejection_reasons.length > 0) ||
      !Number.isSafeInteger(row.review_version) || row.review_version < 1 ||
      typeof row.annotation_session_id !== 'string' || row.annotation_session_id.length > 100 ||
      !Number.isFinite(Date.parse(row.reviewed_at)) ||
      (['a', 'b', 'neither'].includes(row.accepted) && row.preferred !== row.accepted)) {
    throw Error('标注记录格式不正确。');
  }
  const clean = makeReview(manifest, item, {
    choice: choiceOf(row), preferred: row.preferred, reasons: row.rejection_reasons,
    caption: row.caption,
  }, null, row.annotation_session_id);
  clean.review_version = row.review_version;
  clean.reviewed_at = row.reviewed_at;
  return clean;
}

export function rangeRows(reviews, range) {
  return Object.values(reviews)
    .filter(row => row.image_number >= range.start && row.image_number <= range.end)
    .sort((a, b) => a.image_number - b.image_number);
}
