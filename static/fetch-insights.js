'use strict';
(() => {
  const el = (id) => document.getElementById(id);
  let items = [];
  const number = (value) => Number(value || 0).toLocaleString('en-US');
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
      cell(row, item.label, item.sources.join(' · ') || 'Historical inference only');
      cell(row, number(item.listing_operations), `Completed ${number(item.completed_operations)} / Failed ${number(item.failed_operations)} / Unfinished ${number(item.unfinished_operations)}`);
      cell(row, `${number(item.returned_occurrences)} / ${number(item.admitted_occurrences)}`, `Existing admission rate ${ratio(item.existing_occurrence_rate)}`);
      cell(row, number(item.recorded_galleries), `New ${number(item.new_galleries)} / Existing ${number(item.existing_galleries)} / Shared ${number(item.shared_galleries)}`);
      cell(row, `${number(item.positive)} / ${number(item.negative)} / ${number(item.neutral)}`);
      cell(row, `${number(item.unknown)} / ${number(item.duplicate_update)}`);
      cell(row, ratio(item.positive_rate_explicit), `Explicit sample ${number(item.explicit_nonneutral)}`);
      cell(row, item.new_positive_per_completed_operation == null ? '—' : item.new_positive_per_completed_operation.toFixed(2), `${number(item.new_positive)} new positives`);
      cell(row, `${number(item.visible_positive)} / ${number(item.visible_galleries)}`, ratio(item.visible_positive_rate));
      cell(row, number(item.inferred_historical_galleries), 'Cost and outcomes unknown');
      fragment.append(row);
    }
    el('rows').replaceChildren(fragment);
    el('empty').hidden = shown.length !== 0;
    el('empty').textContent = items.length ? 'No matching queries.' : 'No fetch records yet. After recording is enabled, new listing operations will accumulate data.';
  }
  async function refresh() {
    el('refresh').disabled = true;
    el('status').classList.remove('error');
    el('status').textContent = 'Loading statistics…';
    try {
      const response = await fetch('/api/insights/fetch', {cache: 'no-store'});
      if (!response.ok) throw new Error('request failed');
      const data = await response.json();
      if (data.schema !== 'fetch-insights-v1' || !Array.isArray(data.items)) throw new Error('unexpected schema');
      items = data.items;
      el('unique').textContent = number(data.recorded_unique_galleries);
      el('pairs').textContent = number(data.recorded_query_gallery_pairs);
      el('started').textContent = data.measurement_started_at || 'Not started';
      render();
      el('status').textContent = `Updated · ${new Date().toLocaleTimeString('en-US')} · ${items.length} queries`;
    } catch (_) {
      el('status').classList.add('error');
      el('status').textContent = 'Unable to load statistics; existing data may be stale. Retry and check that the service is updated and supports fetch insights.';
    } finally {
      el('refresh').disabled = false;
    }
  }
  el('refresh').addEventListener('click', refresh);
  el('filter').addEventListener('input', render);
  el('sort').addEventListener('change', render);
  refresh();
})();
