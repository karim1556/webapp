(() => {
  const monitor = document.querySelector('#monitor');
  const fields = [...monitor.querySelectorAll('[data-field]')];
  const status = document.querySelector('#statusText');
  const dot = document.querySelector('#statusDot');
  const updated = document.querySelector('#lastReceived');
  const gauge = document.querySelector('#painIndexGauge');
  const assessmentNote = document.querySelector('#assessmentNote');
  const contract = document.querySelector('#readingContract');
  const charts = TelemetryChart.setup(monitor);
  const empty = document.querySelector('#monitorState');
  const emptyTitle = document.querySelector('#monitorStateTitle');
  const emptyMessage = document.querySelector('#monitorStateMessage');
  const hasLiveSource = monitor.dataset.liveSource === 'true';
  const sampleLimit = Number(monitor.dataset.sampleLimit);
  let lastId;
  let busy = false;
  let expiryTimer;

  function setStatus(label, mode) {
    if (status.textContent !== label) status.textContent = label;
    dot.className = 'status-dot ' + mode;
  }

  function paint(reading) {
    fields.forEach(element => {
      const value = reading?.[element.dataset.field];
      const unit = element.dataset.unit;
      if (typeof value === 'boolean') {
        element.textContent = element.dataset.field === 'button_pressed'
          ? (value ? 'Pressed' : 'Released')
          : (value ? 'Detected' : 'Absent');
      } else if (Number.isFinite(value) && unit) {
        element.textContent = TelemetryChart.format(value) + (unit === '%' ? '%' : ' ' + unit);
      } else {
        element.textContent = typeof value === 'string' ? value :
          element.dataset.field === 'posture_status' && value == null ? '' : TelemetryChart.format(value);
      }
    });
    assessmentNote.textContent = reading && !Number.isFinite(reading.pain_index)
      ? 'Score unavailable for this reading'
      : 'Experimental estimate from wearable signals';
    if (gauge) drawPainIndexGauge(gauge, reading?.pain_index);
    updated.textContent = reading ? PhantomTime.formatISTDateTime(reading.ts, { withSeconds: true }) : '—';
  }

  const offlineMessage = 'No recent readings from your wearable. Recorded readings are available in Signal History.';

  function showUnavailable(label, message) {
    window.clearTimeout(expiryTimer);
    setStatus(label, 'stale');
    if (emptyTitle.textContent !== label) emptyTitle.textContent = label;
    if (emptyMessage.textContent !== message) emptyMessage.textContent = message;
    if (lastId !== undefined) {
      paint(null);
      charts.update([], 'No live readings');
    }
    contract.textContent = '';
    empty.hidden = false;
    lastId = undefined;
  }

  async function fetchLive(limit) {
    const startedAt = performance.now();
    const response = await fetch('/api/live?limit=' + limit, { cache: 'no-store' });
    if (response.status === 401) {
      showUnavailable('Sign-in required', 'Sign in again to see live readings.');
      window.location.assign('/login?next=/dashboard');
      return null;
    }
    if (!response.ok) throw new Error('Live telemetry unavailable');
    const data = await response.json();
    return { data, deadline: startedAt + data.fresh_for_ms };
  }

  async function refresh() {
    if (busy || document.hidden || !navigator.onLine) return;
    busy = true;
    try {
      let result = await fetchLive(1);
      if (!result) return;
      if (result.data.status === 'ok' && result.data.reading.id !== lastId) {
        result = await fetchLive(sampleLimit);
        if (!result) return;
      }
      const { data, deadline } = result;
      if (data.status === 'no_data') {
        showUnavailable(data.device_id ? 'Awaiting readings' : 'No device selected',
          data.device_id ? 'No readings have arrived from this wearable.' : 'Select a wearable in Settings.');
        return;
      }
      if (data.status === 'no_live_source') {
        showUnavailable('No live device', 'This patient’s recordings are available in Signal History.');
        return;
      }
      if (data.status === 'offline' || !navigator.onLine || deadline <= performance.now()) {
        showUnavailable('Device offline', offlineMessage);
        return;
      }
      if (data.status !== 'ok' || !data.fresh || !Number.isFinite(deadline)) {
        throw new Error('Live telemetry unavailable');
      }

      empty.hidden = true;
      setStatus('Live', 'active');
      contract.textContent = data.reading.finger_detected === false
        ? 'Finger contact is absent. Derived optical readings are unavailable.' : '';
      if (data.reading.id !== lastId) {
        paint(data.reading);
        charts.update(data.samples);
        lastId = data.reading.id;
      }
      window.clearTimeout(expiryTimer);
      expiryTimer = window.setTimeout(() => showUnavailable('Device offline', offlineMessage),
                                      Math.max(0, deadline - performance.now()));
    } catch {
      showUnavailable('Connection unavailable', 'Live readings are unavailable. Reconnecting…');
    } finally {
      busy = false;
    }
  }

  paint(null);
  charts.update([], 'No live readings');
  if (!hasLiveSource) return;

  refresh();
  document.addEventListener('visibilitychange', refresh);
  window.addEventListener('offline', () => showUnavailable('Connection unavailable', 'Reconnect to see live readings.'));
  window.addEventListener('online', refresh);
  window.setInterval(refresh, Number(monitor.dataset.pollSeconds) * 1000);
})();
