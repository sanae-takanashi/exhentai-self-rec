'use strict';
(() => {
  const el = (id) => document.getElementById(id);
  let items = [];
  const number = (value) => Number(value || 0).toLocaleString('zh-CN');
  const ratio = (value) => value == null ? '—' : `${(value * 100).toFixed(1)}%`;
  const cell = (row, text, detail) => {
    const td = document.createElement('td');
    td.textContent = text;
    if (detail) {
      const small = document.createElement('small');
      small.textContent = detail;
      td.append(small);
    }
    row.append(td);
  };
  function render() {
    const filter = el('filter').value.toLocaleLowerCase();
    const sort = el('sort').value;
    const shown = items.filter((item) => item.label.toLocaleLowerCase().includes(filter))
      .slice().sort((a, b) => b[sort] - a[sort] || a.label.localeCompare(b.label));
    const fragment = document.createDocumentFragment();
    for (const item of shown) {
      const row = document.createElement('tr');
      cell(row, item.label, item.sources.join(' · ') || '仅历史推断');
      cell(row, number(item.listing_operations), `完成 ${number(item.completed_operations)} / 失败 ${number(item.failed_operations)} / 未完成 ${number(item.unfinished_operations)}`);
      cell(row, `${number(item.returned_occurrences)} / ${number(item.admitted_occurrences)}`, `重复入库占比 ${ratio(item.existing_occurrence_rate)}`);
      cell(row, number(item.recorded_galleries), `新增 ${number(item.new_galleries)} / 已存在 ${number(item.existing_galleries)} / 共享 ${number(item.shared_galleries)}`);
      cell(row, `${number(item.positive)} / ${number(item.negative)} / ${number(item.neutral)}`);
      cell(row, `${number(item.unknown)} / ${number(item.duplicate_update)}`);
      cell(row, ratio(item.positive_rate_explicit), `明确样本 ${number(item.explicit_nonneutral)}`);
      cell(row, item.new_positive_per_completed_operation == null ? '—' : item.new_positive_per_completed_operation.toFixed(2), `${number(item.new_positive)} 新增好评`);
      cell(row, `${number(item.visible_positive)} / ${number(item.visible_galleries)}`, ratio(item.visible_positive_rate));
      cell(row, number(item.inferred_historical_galleries), '成本与收益未知');
      fragment.append(row);
    }
    el('rows').replaceChildren(fragment);
    el('empty').hidden = shown.length !== 0;
    el('empty').textContent = items.length ? '没有匹配的查询。' : '暂无采集记录。启用后执行采集，新的列表操作会开始积累数据。';
  }
  async function refresh() {
    el('refresh').disabled = true;
    el('status').classList.remove('error');
    el('status').textContent = '正在读取统计…';
    try {
      const response = await fetch('/api/insights/fetch', {cache: 'no-store'});
      if (!response.ok) throw new Error('request failed');
      const data = await response.json();
      if (data.schema !== 'fetch-insights-v1' || !Array.isArray(data.items)) throw new Error('unexpected schema');
      items = data.items;
      el('unique').textContent = number(data.recorded_unique_galleries);
      el('pairs').textContent = number(data.recorded_query_gallery_pairs);
      el('started').textContent = data.measurement_started_at || '尚未开始记录';
      render();
      el('status').textContent = `已更新 · ${new Date().toLocaleTimeString('zh-CN')} · ${items.length} 个查询`;
    } catch (_) {
      el('status').classList.add('error');
      el('status').textContent = '读取失败；已有数据可能过期。请重试，并确认服务已更新且支持采集洞察。';
    } finally {
      el('refresh').disabled = false;
    }
  }
  el('refresh').addEventListener('click', refresh);
  el('filter').addEventListener('input', render);
  el('sort').addEventListener('change', render);
  refresh();
})();
