const TelemetryChart = (() => {
  const numeric = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });
  const format = value => Number.isFinite(value) ? numeric.format(value) : '—';
  const gapMs = 60000;

  function plotRows(rows, series) {
    const plotted = [];
    let number = 0;
    let previousTime;
    let missing = false;
    for (const row of rows) {
      const time = Date.parse(row.ts);
      if (!series.some(({ field }) => Number.isFinite(row[field]))) {
        missing = true;
        continue;
      }
      if (number && (missing || time - previousTime > gapMs)) {
        plotted.push({ x: number + .5 });
      }
      plotted.push({ x: ++number, time, row });
      previousTime = time;
      missing = false;
    }
    return plotted;
  }

  const medianFields = new Set(['pulse_raw_adc', 'heart_rate', 'hrv_rmssd',
    'spo2', 'perfusion_index', 'respiration_rate']);

  function medianTrace(data) {
    return data.map((point, index) => {
      if (point.y === null) return point;
      // A full window avoids changing samples beside gaps or at session edges.
      const neighbors = data.slice(Math.max(0, index - 2), index + 3);
      if (index < 2 || neighbors.length !== 5 || neighbors.some(neighbor => neighbor.y === null)) return point;
      const mid = neighbors.map(neighbor => neighbor.y).sort((a, b) => a - b)[2];
      return { ...point, raw: point.y, y: mid };
    });
  }

  function renderPanel(panel) {
    const canvas = panel.querySelector('canvas');
    const empty = panel.querySelector('.trend-empty');
    const meta = panel.querySelector('[data-panel-meta]');
    const style = panel.dataset.plotStyle;
    const series = JSON.parse(panel.dataset.series).map(([field, label, unit, color], index) => ({
      field, label, unit, color, axis: index === 0 ? 'y' : 'ySecondary',
      legend: panel.querySelector(`[data-legend-field="${field}"]`),
    }));
    let chart;

    return (rows, message, raw) => {
      const plotted = plotRows(rows, series);
      const latest = plotted.at(-1);
      const available = Boolean(latest) && typeof Chart !== 'undefined';
      canvas.hidden = !available;
      empty.hidden = available;
      meta.textContent = latest
        ? 'Latest plotted · ' + PhantomTime.formatISTDateTime(latest.time) + ' IST'
        : '';
      if (!available) {
        series.forEach(({ legend }) => {
          legend.classList.remove('signal-series-empty');
          legend.querySelector('[data-series-empty]').hidden = true;
        });
        chart?.destroy();
        chart = undefined;
        empty.textContent = latest ? 'The chart could not load.' :
          rows.length ? 'No readings for these signals in this window.' : message;
        return;
      }

      const pointsOnly = style === 'points' && raw;
      const options = chartDefaults();
      options.animation = false;
      options.maintainAspectRatio = false;
      options.scales = {
        x: {
          type: 'linear', min: 0.5, max: latest.x + .5,
          border: { display: false },
          grid: { display: false },
          ticks: { precision: 0, color: '#496462', maxTicksLimit: 5, includeBounds: false },
          title: { display: true, text: 'Reading number', color: '#496462', font: { size: 10 } },
        },
      };
      options.plugins.tooltip.callbacks = {
        title: ([point]) => PhantomTime.formatISTDateTime(point.raw.time, { withSeconds: true }) + ' IST',
        label: point => {
          const item = series[point.datasetIndex];
          const value = item.label + ' · ' + format(point.parsed.y) + ' ' + item.unit;
          return point.raw.raw !== undefined && point.raw.raw !== point.parsed.y
            ? value + ' (device ' + format(point.raw.raw) + ')' : value;
        },
      };
      const datasets = series.map(({ field, label, unit, color, axis, legend }, index) => {
        let data = plotted.map(({ x, time, row }) => ({
          x, time, y: Number.isFinite(row?.[field]) ? row[field] : null,
        }));
        if (!raw && medianFields.has(field)) data = medianTrace(data);
        const hasData = data.some(point => point.y !== null);
        legend.classList.toggle('signal-series-empty', !hasData);
        legend.querySelector('[data-series-empty]').hidden = hasData;
        options.scales[axis] = {
          axis: 'y', position: index ? 'right' : 'left',
          display: hasData,
          border: { display: false },
          grid: { drawOnChartArea: index === 0, color: '#e1ebe8' },
          ticks: { color, maxTicksLimit: 5 },
          title: { display: true, text: unit, color },
          grace: '10%',
        };
        return {
          label, data, yAxisID: axis,
          showLine: !pointsOnly,
          fill: style === 'area' && index === 0 ? 'start' : false,
          cubicInterpolationMode: 'monotone',
          spanGaps: false,
          borderColor: color,
          backgroundColor: color + '20',
          borderWidth: 2.25,
          pointBackgroundColor: color,
          pointBorderColor: '#fff',
          pointBorderWidth: .75,
          pointHoverRadius: 5,
          pointRadius: ({ dataIndex: i }) => pointsOnly ? 2.7 :
            (data[i - 1]?.y == null && data[i + 1]?.y == null) ? 3 : 0,
        };
      });

      if (chart && chart.config.type !== (pointsOnly ? "scatter" : "line")) {
        chart.destroy();
        chart = undefined;
      }
      if (chart) {
        chart.data.datasets = datasets;
        chart.options = options;
        chart.update('none');
      } else {
        chart = new Chart(canvas, {
          type: pointsOnly ? 'scatter' : 'line', data: { datasets }, options,
        });
      }
    };
  }

  function setup(container) {
    const section = container.matches('[data-signal-grid]') ? container : container.querySelector('[data-signal-grid]');
    const panelGrid = section.querySelector('[role="tabpanel"]');
    const tabs = [...section.querySelectorAll('[data-signal-tab]')];
    const rawSwitch = section.querySelector('[data-raw-signals]');
    const panels = [...section.querySelectorAll('[data-signal-panel]')].map(panel => [panel, renderPanel(panel)]);
    let rows = [];
    let message = 'Waiting for readings…';
    let active = 'all';

    function redraw() {
      for (const [panel, render] of panels) {
        const show = active === 'all' || panel.dataset.signalPanel === active;
        panel.hidden = !show;
        if (show) render(rows, message, rawSwitch.checked);
      }
    }

    function selectTab(tab) {
      active = tab.dataset.signalTab;
      tabs.forEach(option => {
        option.setAttribute('aria-selected', option === tab);
        option.tabIndex = option === tab ? 0 : -1;
      });
      panelGrid.setAttribute('aria-labelledby', tab.id);
      panelGrid.dataset.focused = String(active !== 'all');
      redraw();
    }

    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => selectTab(tab));
      tab.addEventListener('keydown', event => {
        const next = { ArrowLeft: (index - 1 + tabs.length) % tabs.length,
          ArrowRight: (index + 1) % tabs.length, Home: 0, End: tabs.length - 1 }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        tabs[next].focus();
        selectTab(tabs[next]);
      });
    });
    rawSwitch.addEventListener('change', redraw);
    return {
      update(samples, emptyMessage = 'No received samples in this window.') {
        rows = samples;
        message = emptyMessage;
        redraw();
      },
    };
  }

  return { setup, format };
})();
