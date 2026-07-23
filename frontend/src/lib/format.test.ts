/**
 * Unit tests for the period-over-period helpers in format.ts.
 * Run with:  npm run test:units   (uses Node's built-in test runner)
 * These cover the pure date/window math so the Explore "vs previous period"
 * feature can't silently drift (e.g. off-by-one on month boundaries).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { prevWindow, pctDelta, dateRange, formatMeasure } from "./format.js";

test("prevWindow: 7-day window ends the day before `from`", () => {
  const { prevFrom, prevTo } = prevWindow("2026-07-08", "2026-07-14");
  assert.equal(prevTo, "2026-07-07"); // day before `from`
  assert.equal(prevFrom, "2026-07-01"); // 7 days before that (inclusive)
  // previous window is the same length
  assert.equal(dateRange(prevFrom, prevTo).length, 7);
});

test("prevWindow: 31-day July window is equal length, not a calendar month", () => {
  const { prevFrom, prevTo } = prevWindow("2026-07-01", "2026-07-31");
  assert.equal(prevTo, "2026-06-30");
  assert.equal(prevFrom, "2026-05-31"); // 31 days inclusive, ends Jun 30
  assert.equal(dateRange(prevFrom, prevTo).length, 31);
});

test("prevWindow: single-day window", () => {
  const { prevFrom, prevTo } = prevWindow("2026-03-15", "2026-03-15");
  assert.equal(prevTo, "2026-03-14");
  assert.equal(prevFrom, "2026-03-14");
});

test("prevWindow: leap-day boundary", () => {
  const { prevFrom, prevTo } = prevWindow("2024-02-29", "2024-03-01");
  assert.equal(prevTo, "2024-02-28");
  assert.equal(prevFrom, "2024-02-27");
});

test("pctDelta: basic increase", () => {
  assert.equal(pctDelta(120, 100), 20);
});

test("pctDelta: decrease is negative", () => {
  assert.equal(pctDelta(80, 100), -20);
});

test("pctDelta: null/zero baseline yields null", () => {
  assert.equal(pctDelta(100, 0), null);
  assert.equal(pctDelta(100, null), null);
  assert.equal(pctDelta(null, 100), null);
});

test("formatMeasure: money keeps two decimals", () => {
  assert.equal(formatMeasure("spend", 1234.5), "1,234.50");
});

test("formatMeasure: ctr renders as percent", () => {
  assert.equal(formatMeasure("ctr", 2.5), "2.50%");
});
