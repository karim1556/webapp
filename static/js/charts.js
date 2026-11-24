/**
 * charts.js — AE490 PhantomLimb
 * Shared Chart.js configuration factory.
 * Must be loaded before dashboard.js / history page scripts.
 */

/**
 * Returns a Chart.js options object for the light instrument UI.
 * @param {string} yLabel  - Y-axis label
 * @param {boolean} legend - Show legend (default false)
 */
function chartDefaults(yLabel = '', legend = false) {
  return {
    responsive: true,
    animation: { duration: 300 },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        display: legend,
        labels: { color: '#385256', boxWidth: 12, font: { family: 'DM Sans', size: 11, weight: '600' } }
      },
      tooltip: {
        backgroundColor: '#ffffff',
        borderColor: '#DDE8E5',
        borderWidth: 1,
        titleColor: '#172a2d',
        bodyColor: '#385256',
        padding: 10,
        cornerRadius: 8,
        titleFont: { family: 'DM Sans', weight: '600' },
        bodyFont: { family: 'DM Sans' },
      },
    },
    scales: {
      x: {
        display: true,
        ticks: { color: '#6F8585', font: { size: 10, family: 'DM Sans' }, maxTicksLimit: 8 },
        grid: { color: '#E7EFEC' },
      },
      y: {
        display: true,
        ticks: { color: '#6F8585', font: { size: 10, family: 'DM Sans' } },
        grid: { color: '#E1EBE8' },
        title: { display: !!yLabel, text: yLabel, color: '#6F8585', font: { size: 10, family: 'DM Sans' } }
      }
    }
  };
}

function drawPainIndexGauge(canvas, value) {
  const ctx = canvas.getContext('2d');
  const { width, height } = canvas;
  const centerX = width / 2;
  const centerY = height - 22;
  const radius = Math.min(centerX, centerY) - 28;
  ctx.clearRect(0, 0, width, height);
  ctx.lineCap = 'round';

  const arc = (progress, color, weight) => {
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius, Math.PI, Math.PI + Math.PI * progress);
    ctx.strokeStyle = color;
    ctx.lineWidth = weight;
    ctx.stroke();
  };
  arc(1, '#cedfd6', 24);
  const hasValue = Number.isFinite(value) && value >= 0 && value <= 100;
  const progress = hasValue ? value / 100 : 0;
  if (progress > 0) {
    const gradient = ctx.createLinearGradient(centerX - radius, 0, centerX + radius, 0);
    gradient.addColorStop(0, '#0b7770');
    gradient.addColorStop(1, '#6eb49f');
    arc(progress, gradient, 24);
  }
  if (!hasValue) return;
  const angle = Math.PI + Math.PI * progress;
  ctx.beginPath();
  ctx.moveTo(centerX, centerY);
  ctx.lineTo(centerX + Math.cos(angle) * (radius - 37), centerY + Math.sin(angle) * (radius - 37));
  ctx.strokeStyle = '#385a55';
  ctx.lineWidth = 4;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(centerX, centerY, 9, 0, Math.PI * 2);
  ctx.fillStyle = '#0b7770';
  ctx.fill();
}
