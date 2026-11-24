(() => {
  const windowSelect = document.querySelector('#historyWindow');
  const refreshButton = document.querySelector('#historyRefresh');
  const body = document.querySelector('#historyTable');
  const status = document.querySelector('#historyStatus');
  const charts = TelemetryChart.setup(document.querySelector('[data-signal-grid]'));
  const columns = JSON.parse(body.dataset.fields);
  const exportLink = document.querySelector('#historyExport');
  const format = TelemetryChart.format;
  let controller;

  function renderTable(rows, message) {
    body.replaceChildren();
    if (!rows.length) {
      const cell = body.insertRow().insertCell();
      cell.colSpan = columns.length + 3;
      cell.textContent = message;
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const sample of [...rows].reverse()) {
      const row = document.createElement('tr');
      row.insertCell().textContent = PhantomTime.formatISTDateTime(sample.ts, { withSeconds: true });
      columns.forEach(key => { row.insertCell().textContent = format(sample[key]); });
      row.insertCell().textContent = sample.button_pressed == null ? '—' : sample.button_pressed ? 'Pressed' : 'Released';
      row.insertCell().textContent = sample.button_event ? "Hardware button" : "—";
      fragment.append(row);
    }
    body.append(fragment);
  }

  function render(rows, message) {
    charts.update(rows, message);
    renderTable(rows, message);
    status.textContent = rows.length ? `${format(rows.length)} received samples in this window` : message;
  }

  async function load() {
    if (!windowSelect.value || !windowSelect.checkValidity()) return;
    controller?.abort();
    controller = new AbortController();
    const { signal } = controller;
    render([], 'Loading readings…');
    refreshButton.disabled = true;
    const exportUrl = new URL(exportLink.href);
    const period = windowSelect.type === 'date' ? 'day' : 'hours';
    exportUrl.searchParams.set(period, windowSelect.value);
    exportLink.href = exportUrl.href;
    try {
      const response = await fetch(exportLink.dataset.historyApi + '?' + exportUrl.searchParams.toString(), { signal, cache: 'no-store' });
      if (response.status === 401) {
        window.location.assign(exportLink.dataset.signIn);
        return;
      }
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (signal.aborted) return;
      render(data.data, data.device_id ? 'No wearable readings in this time window.' : 'Select a wearable in Settings.');
    } catch {
      if (!signal.aborted) render([], 'Could not load telemetry. Refresh to try again.');
    } finally {
      if (!signal.aborted) refreshButton.disabled = false;
    }
  }

  windowSelect.addEventListener('change', load);
  refreshButton.addEventListener('click', load);
  load();
})();
