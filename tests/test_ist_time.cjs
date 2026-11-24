const test = require('node:test');
const assert = require('node:assert/strict');

const {
  TIME_ZONE,
  formatISTDateTime,
  formatISTTime,
  getISTGreeting,
  parseAppTimestamp,
} = require('../static/js/timezone.js');

test('formats the dashboard clock in India Standard Time', () => {
  const utcMorning = new Date('2026-09-12T04:26:00.000Z');
  assert.equal(TIME_ZONE, 'Asia/Kolkata');
  assert.equal(formatISTTime(utcMorning), '09:56');
  assert.equal(formatISTDateTime(utcMorning), '12 Sept 2026, 09:56');
  assert.equal(getISTGreeting(utcMorning), 'Good morning');
});

test('treats database timestamps without an offset as IST values', () => {
  const parsed = parseAppTimestamp('2026-09-12 09:56:00');
  assert.equal(parsed.toISOString(), '2026-09-12T04:26:00.000Z');
});
