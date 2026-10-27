"use strict";
const assert = require("node:assert/strict");
function validDate(s) { const d = new Date(s + "T00:00:00Z"); return Number.isFinite(d.getTime()) && d.toISOString().slice(0,10) === s; }
assert.equal(validDate("2026-02-30"), false);
assert.equal(validDate("2026-09-30"), true);
assert.equal(validDate("invalid"), false);
console.log("Calendar date checks passed");
