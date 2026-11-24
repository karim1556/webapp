/**
 * PhantomLimb time helpers.
 *
 * The dashboard is an India-based product, so UI timestamps must not depend on
 * the browser's machine timezone. Database timestamps without an offset are
 * also interpreted as IST before they are formatted.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.PhantomTime = api;
})(typeof window !== 'undefined' ? window : typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const TIME_ZONE = 'Asia/Kolkata';

  function parseAppTimestamp(value) {
    if (value instanceof Date) return new Date(value.getTime());
    if (typeof value === 'number') return new Date(value);
    if (value === null || value === undefined || value === '') return new Date(NaN);

    const raw = String(value).trim();
    if (/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw)) return new Date(raw);

    // SQLite timestamps are stored without an offset. They represent the
    // application's configured timezone, not the viewer's local timezone.
    return new Date(`${raw.replace(' ', 'T')}+05:30`);
  }

  function formatParts(value) {
    const date = parseAppTimestamp(value);
    if (Number.isNaN(date.getTime())) return {};

    return new Intl.DateTimeFormat('en-IN', {
      timeZone: TIME_ZONE,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).formatToParts(date).reduce((result, part) => {
      result[part.type] = part.value;
      return result;
    }, {});
  }

  function getISTHour(value) {
    const hour = Number(formatParts(value).hour);
    return Number.isFinite(hour) ? hour : null;
  }

  function getISTGreeting(value) {
    const hour = getISTHour(value);
    if (hour === null) return 'Hello';
    if (hour < 12) return 'Good morning';
    if (hour < 17) return 'Good afternoon';
    return 'Good evening';
  }

  function formatISTTime(value, { withSeconds = false } = {}) {
    const date = parseAppTimestamp(value);
    if (Number.isNaN(date.getTime())) return '—';

    return new Intl.DateTimeFormat('en-IN', {
      timeZone: TIME_ZONE,
      hour: '2-digit',
      minute: '2-digit',
      ...(withSeconds ? { second: '2-digit' } : {}),
      hour12: false,
    }).format(date);
  }

  function formatISTDateTime(value, { withSeconds = false } = {}) {
    const date = parseAppTimestamp(value);
    if (Number.isNaN(date.getTime())) return '—';

    return new Intl.DateTimeFormat('en-IN', {
      timeZone: TIME_ZONE,
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      ...(withSeconds ? { second: '2-digit' } : {}),
      hour12: false,
    }).format(date);
  }

  return {
    TIME_ZONE,
    formatISTDateTime,
    formatISTTime,
    getISTGreeting,
    parseAppTimestamp,
  };
});
