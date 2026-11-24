(function () {
  'use strict';

  const PARTS = [
    { key: 'Night', label: 'Night', range: '00:00–05:59', start: 0, end: 6 },
    { key: 'Morning', label: 'Morning', range: '06:00–11:59', start: 6, end: 12 },
    { key: 'Afternoon', label: 'Afternoon', range: '12:00–17:59', start: 12, end: 18 },
    { key: 'Evening', label: 'Evening', range: '18:00–23:59', start: 18, end: 24 },
  ];
  const COLORS = { teal: '#157c78', blue: '#4776a8', orange: '#c67b37', line: '#dfe8e5', ink: '#385256', muted: '#6f8585' };
  const $ = (id) => document.getElementById(id);
  const charts = {};
  let reportMode = 'check_in';

  function browserTimezone() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Kolkata'; }
    catch (_) { return 'Asia/Kolkata'; }
  }

  function toLocalInputValue(date) {
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }

  function fromLocalInput(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  }

  function localHour(instant, zone) {
    try {
      const pieces = new Intl.DateTimeFormat('en-GB', {
        timeZone: zone || 'Asia/Kolkata', hour: '2-digit', hourCycle: 'h23',
      }).formatToParts(new Date(instant));
      const item = pieces.find((part) => part.type === 'hour');
      return item ? Number(item.value) : null;
    } catch (_) { return null; }
  }

  function daypartFor(instant, zone) {
    const hour = localHour(instant, zone);
    return PARTS.find((part) => hour !== null && hour >= part.start && hour < part.end) || null;
  }

  function reportDate(value, zone, includeTime) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'Time unavailable';
    try {
      return new Intl.DateTimeFormat(undefined, {
        timeZone: zone || 'Asia/Kolkata',
        day: 'numeric', month: 'short', ...(includeTime ? { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' } : {}),
      }).format(date);
    } catch (_) { return date.toLocaleString(); }
  }

  function sensorHourDate(value) {
    if (!value) return null;
    const date = new Date(String(value).replace(' ', 'T') + '+05:30');
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function sensorEpochLabel(value) {
    const date = new Date(Number(value));
    if (Number.isNaN(date.getTime())) return '—';
    return new Intl.DateTimeFormat(undefined, { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date);
  }

  function number(value, digits) {
    if (value == null || value === '') return '—';
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits == null ? 1 : digits) : '—';
  }

  function sensorSampleTime(value, zone) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return '—';
    try {
      return new Intl.DateTimeFormat(undefined, {
        timeZone: zone || 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
      }).format(date);
    } catch (_) { return date.toLocaleTimeString(); }
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[char]));
  }

  function replaceChart(key, canvasId, config) {
    if (charts[key]) charts[key].destroy();
    charts[key] = null;
    const canvas = $(canvasId);
    if (canvas && !canvas.hidden && window.Chart) charts[key] = new Chart(canvas, config);
  }

  function baseChartOptions(yTitle, yMin, yMax) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      plugins: {
        legend: { labels: { color: COLORS.ink, usePointStyle: true, boxWidth: 8, padding: 16, font: { family: 'DM Sans', size: 11 } } },
        tooltip: { titleColor: '#fff', bodyColor: '#fff', backgroundColor: '#183b40', padding: 10 },
      },
      scales: {
        x: { type: 'linear', grid: { color: COLORS.line }, ticks: { color: COLORS.muted, maxTicksLimit: 7, callback: (value) => new Intl.DateTimeFormat(undefined, { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', hour: '2-digit', hourCycle: 'h23' }).format(new Date(Number(value))) } },
        y: { min: yMin, max: yMax, title: { display: Boolean(yTitle), text: yTitle, color: COLORS.muted }, grid: { color: COLORS.line }, ticks: { color: COLORS.muted } },
      },
    };
  }

  function chartNoData(canvasId, hasData, emptyMessage) {
    const canvas = $(canvasId);
    if (!canvas) return;
    const parent = canvas.parentElement;
    let empty = parent.querySelector('.pain-chart-empty');
    if (!hasData) {
      if (!empty) {
        empty = document.createElement('p');
        empty.className = 'pain-chart-empty';
        parent.appendChild(empty);
      }
      empty.textContent = emptyMessage || 'No data in this period yet.';
      canvas.hidden = true;
    } else {
      if (empty) empty.remove();
      canvas.hidden = false;
    }
  }

  async function readJsonResponse(response) {
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.toLowerCase().includes('application/json')) {
      if (response.status === 404) throw new Error('Restart the local app server to load the new pain-diary API routes.');
      throw new Error('The server did not return diary data. Restart the local app server and try again.');
    }
    return response.json();
  }

  function summarizeDayparts(reports) {
    const summary = Object.fromEntries(PARTS.map((part) => [part.key, { count: 0, total: 0, ratings: [] }]));
    reports.forEach((report) => {
      const part = daypartFor(report.occurred_at_utc, report.timezone);
      const rating = report.pain_rating == null || report.pain_rating === '' ? null : Number(report.pain_rating);
      if (part) {
        summary[part.key].count += 1;
        if (Number.isFinite(rating)) {
          summary[part.key].total += rating;
          summary[part.key].ratings.push(rating);
        }
      }
    });
    return summary;
  }

  function renderSummary(reports) {
    const ratings = reports
      .filter((report) => report.pain_rating != null && report.pain_rating !== '')
      .map((report) => Number(report.pain_rating))
      .filter(Number.isFinite);
    const episodes = reports.filter((report) => report.report_type === 'episode').length;
    const sosEvents = reports.filter((report) => report.report_type === 'sos').length;
    const parts = summarizeDayparts(reports);
    const sortedCounts = PARTS.map((part) => ({ key: part.key, count: parts[part.key].count })).sort((a, b) => b.count - a.count);
    const mostLogged = sortedCounts[0] && sortedCounts[0].count && sortedCounts[1].count === sortedCounts[0].count ? 'Tie' : (sortedCounts[0] && sortedCounts[0].count ? sortedCounts[0].key : '—');

    $('reportCount').textContent = String(reports.length);
    $('reportedAverage').textContent = ratings.length ? number(ratings.reduce((sum, value) => sum + value, 0) / ratings.length, 1) : '—';
    $('episodeCount').textContent = String(episodes);
    $('sosEventCount').textContent = String(sosEvents);
    $('topDaypart').textContent = mostLogged;
    $('reportListCount').textContent = reports.length + (reports.length === 1 ? ' report' : ' reports');

    $('daypartSummary').innerHTML = PARTS.map((part) => {
      const item = parts[part.key];
      const avg = item.ratings.length ? number(item.total / item.ratings.length, 1) + ' / 10' : 'No rated reports';
      return '<article class="pain-daypart-tile"><span>' + escapeHtml(part.label) + '</span><strong>' + avg + '</strong><small>' + item.count + (item.count === 1 ? ' report' : ' reports') + ' · ' + part.range + '</small></article>';
    }).join('');

    const countData = PARTS.map((part) => parts[part.key].count);
    const averageData = PARTS.map((part) => parts[part.key].ratings.length ? parts[part.key].total / parts[part.key].ratings.length : null);
    chartNoData('daypartCountChart', reports.length > 0);
    chartNoData('daypartAverageChart', ratings.length > 0, 'No rated pain reports in this period. Archived button events have no rating.');
    replaceChart('daypartCount', 'daypartCountChart', {
      type: 'bar',
      data: { labels: PARTS.map((part) => part.label), datasets: [{ label: 'Reports logged', data: countData, backgroundColor: 'rgba(21,124,120,.72)', borderRadius: 5 }] },
      options: { ...baseChartOptions('Report count', 0), scales: { x: { grid: { display: false }, ticks: { color: COLORS.muted } }, y: { beginAtZero: true, ticks: { precision: 0, color: COLORS.muted }, grid: { color: COLORS.line } } }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (context) => context.parsed.y + (context.parsed.y === 1 ? ' report' : ' reports') } } } },
    });
    replaceChart('daypartAverage', 'daypartAverageChart', {
      type: 'bar',
      data: { labels: PARTS.map((part) => part.label), datasets: [{ label: 'Average self-reported rating', data: averageData, backgroundColor: 'rgba(71,118,168,.72)', borderRadius: 5 }] },
      options: { ...baseChartOptions('Average rating', 0, 10), scales: { x: { grid: { display: false }, ticks: { color: COLORS.muted } }, y: { min: 0, max: 10, ticks: { stepSize: 2, color: COLORS.muted }, grid: { color: COLORS.line } } }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (context) => context.parsed.y == null ? 'No rated reports' : number(context.parsed.y, 1) + ' / 10 · n=' + parts[PARTS[context.dataIndex].key].ratings.length } } } },
    });
  }

  function renderPainTimeline(reports) {
    const rated = reports.filter((report) => report.pain_rating != null && report.pain_rating !== '' && Number.isFinite(Number(report.pain_rating)) && Number.isFinite(Date.parse(report.occurred_at_utc)));
    const checkIns = rated.filter((report) => report.report_type === 'check_in');
    const episodes = rated.filter((report) => report.report_type === 'episode');
    const hasData = checkIns.length + episodes.length > 0;
    chartNoData('painTimelineChart', hasData, reports.some((report) => report.report_type === 'sos') ? 'Archived button events retain their timestamps.' : 'No rated pain reports in this period yet.');
    const dataset = (label, records, color) => ({
      label,
      data: records.map((report) => ({ x: Date.parse(report.occurred_at_utc), y: Number(report.pain_rating), report })).sort((a, b) => a.x - b.x),
      showLine: false,
      pointRadius: 5,
      pointHoverRadius: 7,
      backgroundColor: color,
      borderColor: color,
    });
    replaceChart('painTimeline', 'painTimelineChart', {
      type: 'scatter',
      data: { datasets: [dataset('Check-in', checkIns, COLORS.teal), dataset('Episode', episodes, COLORS.orange)] },
      options: {
        ...baseChartOptions('Pain rating · 0–10', 0, 10),
        scales: { ...baseChartOptions().scales, x: { ...baseChartOptions().scales.x, title: { display: true, text: 'Date and time', color: COLORS.muted } }, y: { min: 0, max: 10, ticks: { stepSize: 2, color: COLORS.muted }, title: { display: true, text: 'Self-reported rating', color: COLORS.muted }, grid: { color: COLORS.line } } },
        plugins: { ...baseChartOptions().plugins, tooltip: { callbacks: { title: (items) => { const report = items[0] && items[0].raw && items[0].raw.report; return report ? reportDate(report.occurred_at_utc, report.timezone, true) : ''; }, label: (context) => context.dataset.label + ' · ' + number(context.parsed.y, 0) + ' / 10' } } },
      },
    });
  }

  function localHourPoints(rows, field) {
    const countField = field.replace(/^(avg|peak)_/, 'count_');
    const mapped = new Map();
    rows.forEach((row) => {
      const date = sensorHourDate(row.hour_start_local);
      if (date) mapped.set(date.getTime(), row);
    });
    const times = Array.from(mapped.keys()).sort((a, b) => a - b);
    if (!times.length) return [];
    const points = [];
    for (let hour = times[0]; hour <= times[times.length - 1]; hour += 3600000) {
      const row = mapped.get(hour);
      points.push({ x: hour, y: row ? (row[field] == null ? null : Number(row[field])) : null, sampleCount: row?.[countField] });
    }
    return points;
  }

  function renderSensorChart(key, canvasId, rows, averageField, peakField, averageLabel, peakLabel, color, yTitle, yMax) {
    const hasData = rows.some((row) => row[averageField] != null || row[peakField] != null);
    chartNoData(canvasId, hasData);
    const options = baseChartOptions(yTitle, 0, yMax);
    replaceChart(key, canvasId, {
      type: 'line',
      data: { datasets: [
        { label: averageLabel, data: localHourPoints(rows, averageField), borderColor: color, backgroundColor: color, pointRadius: 2, pointHoverRadius: 4, borderWidth: 2, cubicInterpolationMode: 'monotone', spanGaps: false },
        { label: peakLabel, data: localHourPoints(rows, peakField), borderColor: color, backgroundColor: color, pointRadius: 2, pointHoverRadius: 4, borderWidth: 1.5, borderDash: [5, 4], cubicInterpolationMode: 'monotone', spanGaps: false },
      ] },
      options: { ...options, plugins: { ...options.plugins, tooltip: { callbacks: { title: (items) => items[0] ? sensorEpochLabel(items[0].parsed.x) : '', label: (context) => context.dataset.label + ': ' + number(context.parsed.y, 1) + (context.raw.sampleCount == null ? '' : ' · n=' + context.raw.sampleCount + ' samples') } } } },
    });
  }

  function painTypeLabel(value) {
    return ({ phantom: 'Phantom limb', residual_limb: 'Residual limb', both: 'Both', other: 'Other', not_sure: 'Not sure' })[value] || '';
  }

  function qualityLabel(value) {
    return ({ burning: 'Burning', electric_shooting: 'Electric / shooting', cramping: 'Cramping', tingling: 'Tingling', aching: 'Aching', pressure: 'Pressure', other: 'Other' })[value] || value;
  }

  function sensorVector(sample, prefix) {
    return ['x', 'y', 'z'].map((axis) => number(sample[prefix + '_' + axis], 2)).join(', ');
  }

  function renderSensorReadings(report) {
    const samples = Array.isArray(report.sensor_readings) ? report.sensor_readings : [];
    const target = Math.max(1, Number(report.sensor_sample_target) || 10);
    const windowSeconds = Math.max(1, Number(report.sensor_window_seconds) || 30);
    if (!samples.length) {
      return '<p class="pain-sos-readings-empty">No sensor readings were attached to this archived event.</p>';
    }

    const received = samples.some(sample => sample.source === 'supabase');
    const columns = received
      ? [['HR (bpm)', 'heart_rate'], ['SpO₂ (%)', 'spo2'], ['HRV RMSSD (ms)', 'hrv_ms'],
        ['Respiration (/min)', 'breath_rate'], ['EMG tone', 'emg_rms'], ['Posture', 'posture_score']]
      : [['HR (bpm)', 'heart_rate'], ['HRV (ms)', 'hrv_ms'], ['Breaths/min', 'breath_rate'],
        ['Breath depth', 'breath_depth'], ['EMG RMS', 'emg_rms'], ['Posture', 'posture_score']];
    const header = '<th scope="col">#</th><th scope="col">Time</th>' +
      columns.map(([label]) => '<th scope="col">' + label + '</th>').join('') +
      (received ? '' : '<th scope="col">Accel (x, y, z)</th><th scope="col">Gyro (x, y, z)</th><th scope="col">Leads</th>');
    const rows = samples.map((sample, index) => {
      const cells = [sample.sample_number || index + 1, sensorSampleTime(sample.occurred_at_utc, report.timezone),
        ...columns.map(([, field]) => number(sample[field], 1))];
      if (!received) {
        const leads = sample.leads_on == null ? '—' : (Number(sample.leads_on) ? 'On' : 'Off');
        cells.push(sensorVector(sample, 'accel'), sensorVector(sample, 'gyro'), leads);
      }
      return '<tr>' + cells.map(cell => '<td>' + escapeHtml(cell) + '</td>').join('') + '</tr>';
    }).join('');
    const captureNote = samples.length < target
      ? 'Only ' + samples.length + ' of ' + target + ' target readings were available in the preceding ' + windowSeconds + ' seconds.'
      : 'The 10 newest readings available in the preceding ' + windowSeconds + ' seconds are shown.';

    return '<details class="pain-sos-readings"><summary>View sensor readings (' + samples.length + '/' + target + ')</summary>' +
      '<p class="pain-sos-readings-note">' + escapeHtml(captureNote) + '</p>' +
      '<div class="pain-sos-samples-scroll" tabindex="0" role="region" aria-label="Scrollable sensor readings table"><table class="pain-sos-samples-table" aria-label="Sensor readings captured with this archived event">' +
      '<thead><tr>' + header + '</tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div></details>';
  }

  function renderReportList(reports) {
    const container = $('painReportList');
    if (!reports.length) {
      container.innerHTML = '<div class="pain-report-empty"><strong>No pain reports in this period.</strong><span>Add a rated check-in or episode to record what you felt.</span></div>';
      return;
    }
    container.innerHTML = reports.map((report) => {
      const isSos = report.report_type === 'sos';
      const type = isSos ? 'Archived button event' : (report.report_type === 'episode' ? 'Pain episode' : 'Check-in');
      const when = reportDate(report.occurred_at_utc, report.timezone, true);
      const details = [];
      if (report.pain_type) details.push(painTypeLabel(report.pain_type));
      if (Array.isArray(report.qualities) && report.qualities.length) details.push(report.qualities.map(qualityLabel).join(', '));
      if (report.report_type === 'episode' && report.started_at_utc && report.ended_at_utc) {
        details.push('Ended ' + reportDate(report.ended_at_utc, report.timezone, true));
      } else if (report.report_type === 'episode') details.push('End time not recorded');
      if (report.activity) details.push('Before: ' + report.activity);
      if (report.relief) details.push('Helped: ' + report.relief);
      const note = report.notes ? '<p class="pain-report-notes">' + escapeHtml(report.notes) + '</p>' : '';
      const legacy = report.legacy ? '<span class="pain-report-legacy">Earlier diary</span>' : '';
      const sensorReadings = isSos ? renderSensorReadings(report) : '';
      const rating = report.pain_rating == null || report.pain_rating === ''
        ? '<strong class="pain-sos-rating">Time recorded<small> · no rating entered</small></strong>'
        : '<strong class="pain-report-rating">' + escapeHtml(report.pain_rating) + '<small> / 10</small></strong>';
      return '<article class="pain-report-row">' +
        '<div class="pain-report-row-main"><div class="pain-report-row-meta"><span class="pain-report-kind' + (isSos ? ' pain-report-kind--sos' : '') + '">' + type + '</span>' + legacy + '<time>' + escapeHtml(when) + '</time></div>' +
        rating +
        (details.length ? '<p class="pain-report-details">' + details.map(escapeHtml).join(' · ') + '</p>' : '') + note + sensorReadings +
        '</div></article>';
    }).join('');
  }

  function renderHardwareEvents(events, status) {
    const container = $('hardwareEventsList');
    const stampSection = $('hardwarePainStamps');
    const fields = JSON.parse(stampSection.dataset.fields);
    const archive = stampSection.dataset.archive === 'true';
    container.replaceChildren();
    if (!events.length) {
      container.textContent = status === 'unavailable'
        ? 'Hardware events are temporarily unavailable.'
        : 'No hardware pain stamps received in this period.';
      return;
    }
    for (const event of events) {
      const details = document.createElement('details');
      details.className = 'review-entry';
      const summary = document.createElement('summary');
      summary.textContent = (archive ? 'Archived SOS · ' : 'Hardware button · ') + PhantomTime.formatISTDateTime(event.occurred_at, {withSeconds: true}) + ' IST';
      details.append(summary);
      const source = document.createElement('p');
      source.className = 'page-sub';
      source.textContent = archive ? 'Timestamp from the original recording.' : 'Timestamp of the first pressed packet received in Supabase.';
      details.append(source);
      const snapshot = document.createElement('dl');
      snapshot.className = 'reading-snapshot';
      for (const [key, label, unit] of fields) {
        const group = document.createElement('div');
        const term = document.createElement('dt');
        const value = document.createElement('dd');
        term.textContent = label;
        const reading = event.measurements[key];
        value.textContent = reading == null ? '—' : number(reading, 2) + ' ' + unit;
        group.append(term, value);
        snapshot.append(group);
      }
      details.append(snapshot);
      container.append(details);
    }
  }

  function render(data) {
    const reports = data.reports || [];
    const sensors = data.sensor_hours || [];
    const telemetryStatus = $('diaryTelemetryStatus');
    telemetryStatus.textContent = data.sensor_status === 'archive' ? 'Open Signal History to review the archived sensor recordings by date.' : data.sensor_status === 'unavailable'
      ? 'Supabase telemetry is temporarily unavailable. Saved diary entries are shown below.'
      : data.sensor_truncated ? 'Showing hourly summaries for the latest 2,000 readings.' : '';
    telemetryStatus.hidden = !telemetryStatus.textContent;
    const days = Number(data.days) || 14;
    $('reportWindowLabel').textContent = 'Last ' + days + (days === 1 ? ' day' : ' days');
    renderSummary(reports);
    renderPainTimeline(reports);
    renderSensorChart('heartTrend', 'heartTrendChart', sensors, 'avg_heart_rate', 'peak_heart_rate', 'Hourly average', 'Highest received sample in hour', COLORS.orange, 'BPM', undefined);
    renderSensorChart('breathTrend', 'breathTrendChart', sensors, 'avg_breath_rate', 'peak_breath_rate', 'Hourly average', 'Highest received sample in hour', COLORS.blue, 'Breaths/min', undefined);
    renderSensorChart('postureTrend', 'postureTrendChart', sensors, 'avg_posture_score', 'peak_posture_score', 'Hourly average', 'Highest received sample in hour', COLORS.teal, 'Score', undefined);
    renderSensorChart('emgTrend', 'emgTrendChart', sensors, 'avg_emg_rms', 'peak_emg_rms', 'Hourly average', 'Highest received sample in hour', COLORS.orange, 'Device units', undefined);
    renderReportList(reports);
    renderHardwareEvents(data.hardware_events || [], data.hardware_events_status);
  }

  async function loadTrends() {
    const days = Number($('trendDays').value) || 14;
    $('painReportList').innerHTML = '<p class="table-loading">Loading your reports…</p>';
    try {
      const response = await fetch('/api/pain-trends?days=' + encodeURIComponent(days), { cache: 'no-store', headers: { Accept: 'application/json' } });
      const body = await readJsonResponse(response);
      if (!response.ok || body.status !== 'ok') throw new Error(body.msg || 'Could not load your reports.');
      render(body);
    } catch (error) {
      renderHardwareEvents([], 'unavailable');
      $('painReportList').innerHTML = '<div class="pain-report-empty"><strong>Could not load reports.</strong><span>' + escapeHtml(error.message) + '</span></div>';
    }
  }

  function updateReportMode(mode) {
    reportMode = mode;
    document.querySelectorAll('[data-report-mode]').forEach((button) => {
      const selected = button.dataset.reportMode === mode;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-selected', String(selected));
    });
    $('painRatingPrompt').textContent = mode === 'episode' ? 'What was the highest pain during this episode?' : 'How much pain are you feeling right now?';
    $('checkInTimeField').hidden = mode !== 'check_in';
    $('episodeTimeFields').hidden = mode !== 'episode';
    $('episodeStartedAt').required = mode === 'episode';
    $('painReportMessage').textContent = '';
  }

  function setPanel(open) {
    const panel = $('painReportPanel');
    panel.hidden = !open;
    $('togglePainReport').setAttribute('aria-expanded', String(open));
    if (open) {
      if (!$('occurredAt').value) $('occurredAt').value = toLocalInputValue(new Date());
      if (!$('episodeStartedAt').value) $('episodeStartedAt').value = toLocalInputValue(new Date());
      panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  async function saveReport(event) {
    event.preventDefault();
    const message = $('painReportMessage');
    const button = $('savePainReport');
    const rating = Number($('painRating').value);
    const occurredAt = fromLocalInput($('occurredAt').value);
    const startedAt = fromLocalInput($('episodeStartedAt').value);
    const endedAt = $('episodeEndedAt').value ? fromLocalInput($('episodeEndedAt').value) : null;

    if (reportMode === 'check_in' && !occurredAt) { message.textContent = 'Choose the check-in time.'; $('occurredAt').focus(); return; }
    if (reportMode === 'episode' && !startedAt) { message.textContent = 'Choose when the episode started.'; $('episodeStartedAt').focus(); return; }
    if (reportMode === 'episode' && endedAt && Date.parse(endedAt) < Date.parse(startedAt)) { message.textContent = 'The end time must be after the start time.'; $('episodeEndedAt').focus(); return; }

    const qualities = Array.from(document.querySelectorAll('input[name="painQuality"]:checked')).map((input) => input.value);
    const payload = {
      report_type: reportMode,
      pain_rating: rating,
      timezone: browserTimezone(),
      occurred_at: occurredAt,
      started_at: startedAt,
      ended_at: endedAt,
      pain_type: $('painType').value,
      qualities,
      activity: $('painActivity').value,
      relief: $('painRelief').value,
      notes: $('painNotes').value,
    };

    button.disabled = true;
    message.textContent = 'Saving…';
    try {
      const response = await fetch('/api/pain-reports', {
        method: 'POST',
        headers: { 'X-CSRF-Token': document.querySelector('meta[name=csrf-token]').content, 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(payload),
      });
      const body = await readJsonResponse(response);
      if (!response.ok || body.status !== 'ok') throw new Error(body.msg || 'Could not save report.');
      message.textContent = 'Report saved.';
      $('painReportForm').reset();
      $('painRatingValue').textContent = '0';
      $('occurredAt').value = toLocalInputValue(new Date());
      $('episodeStartedAt').value = toLocalInputValue(new Date());
      $('episodeEndedAt').value = '';
      await loadTrends();
      message.textContent = 'Report saved.';
    } catch (error) {
      message.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('painRating').addEventListener('input', (event) => { $('painRatingValue').textContent = event.target.value; });
    $('togglePainReport').addEventListener('click', () => setPanel($('painReportPanel').hidden));
    $('closePainReport').addEventListener('click', () => setPanel(false));
    $('trendDays').addEventListener('change', loadTrends);
    $('painReportForm').addEventListener('submit', saveReport);
    if (location.hash === '#painReportPanel') setPanel(true);
    document.querySelectorAll('[data-report-mode]').forEach((button) => button.addEventListener('click', () => updateReportMode(button.dataset.reportMode)));
    $('occurredAt').value = toLocalInputValue(new Date());
    $('episodeStartedAt').value = toLocalInputValue(new Date());
    loadTrends();
  });
})();
