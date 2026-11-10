'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const displayJS = fs.readFileSync(path.join(root, 'web/display.js'), 'utf8');
const displayCSS = fs.readFileSync(path.join(root, 'web/display.css'), 'utf8');

function makeHarness() {
  const elements = new Map();
  const timers = [];
  const intervals = [];
  const errors = [];
  function element(id) {
    if (!elements.has(id)) {
      let content = '';
      const listeners = new Map();
      const item = {
        id, style: {}, hidden: false, className: '', writes: 0,
        classList: { add() {}, remove() {} },
        addEventListener(name, callback) { listeners.set(name, callback); },
        dispatch(name) { listeners.get(name)?.(); },
        replaceChildren() {},
        appendChild() {},
        querySelectorAll() { return []; },
      };
      Object.defineProperty(item, 'textContent', {
        get() { return content; },
        set(value) { item.writes++; content = value; },
      });
      elements.set(id, item);
    }
    return elements.get(id);
  }
  const video = element('video');
  video._hasSrc = false;
  video.loadCalls = 0;
  video.pauseCalls = 0;
  video.pending = [];
  video.error = null;
  Object.defineProperty(video, 'src', {
    get() { return video._src; },
    set(value) { video._src = value; video._hasSrc = true; },
  });
  video.hasAttribute = name => name === 'src' && video._hasSrc;
  video.removeAttribute = name => { if (name === 'src') video._hasSrc = false; };
  video.load = () => { video.loadCalls++; };
  video.pause = () => { video.pauseCalls++; };
  video.play = () => new Promise((resolve, reject) => video.pending.push({resolve, reject}));
  video.canPlayType = () => 'probably';
  const publicState = {
    settings: {
      timezone: 'UTC', time_format: '24', city: 'Саяногорск',
      night_mode: false, night_start: 23, night_end: 6, night_opacity: .7,
      clock_shift: false, show_clock: true, show_date: true, show_weather: true,
      clock_position: 'bottom-left', video_mode: 'ordered', video_interval_minutes: 0,
      online_sources: {apple: true},
    },
    weather: {available: false}, videos: [], modules: [], reload_counter: 0,
  };
  let nextTimer = 0;
  const context = {
    document: {getElementById: element, createElement: () => element('module')},
    setInterval: (fn, ms) => intervals.push({fn, ms}),
    setTimeout: (fn, ms) => {
      const timer = {id: ++nextTimer, fn, ms, cleared: false};
      timers.push(timer);
      return timer.id;
    },
    clearTimeout: id => {
      const timer = timers.find(t => t.id === id);
      if (timer) timer.cleared = true;
    },
    fetch: async () => ({ok: true, json: async () => ({
      ...publicState, settings: {...publicState.settings},
      videos: [...publicState.videos], modules: [...publicState.modules],
    })}),
    console: {error: (...items) => errors.push(items), warn() {}},
    location: {reload() {}},
  };
  vm.createContext(context);
  vm.runInContext(displayJS, context, {filename: 'display.js'});
  return {context, elements, video, timers, intervals, errors, publicState};
}
async function settle() {
  await new Promise(resolve => setImmediate(resolve));
}
async function ready() {
  const harness = makeHarness();
  await settle(); // Initial asynchronous poll invoked by display.js
  return harness;
}

test('idle background has no perpetually running CSS animation', () => {
  assert.doesNotMatch(displayCSS, /animation:\s*float\b/);
  assert.doesNotMatch(displayCSS, /@keyframes\s+float\b/);
  assert.match(displayCSS, /#video\.ready/); // Video fade-in remains available
});

test('clock is polled every ten seconds and unchanged text is not rewritten', async () => {
  const h = await ready();
  assert.ok(h.intervals.some(timer => timer.ms === 10000));
  assert.ok(h.intervals.some(timer => timer.ms === 30000));
  const ids = ['clock', 'date', 'city', 'wxTemp', 'wxDetail', 'wxIcon'];
  const before = ids.map(id => h.elements.get(id).writes);
  h.context.drawTime();
  h.context.showWeather();
  assert.deepEqual(ids.map(id => h.elements.get(id).writes), before);
});

test('two signals for a single invalid video schedule one fallback', async () => {
  const h = await ready();
  h.publicState.videos = [{id: 'bad', url: '/media/online/bad', provider: 'apple'}];
  await h.context.poll();
  assert.equal(h.video.pending.length, 1);
  h.video.error = {code: 4, message: 'Format unsupported'};
  h.video.dispatch('error');
  h.video.pending[0].reject(new Error('Format unsupported'));
  await settle();
  assert.equal(h.errors.length, 1);
  assert.equal(h.timers.filter(t => t.ms === 4000).length, 1);
});

test('idle player does not repeatedly load empty media and resumes on new clip', async () => {
  const h = await ready();
  assert.equal(h.video.loadCalls, 0);
  h.context.changeVideo();
  assert.equal(h.video.loadCalls, 0);
  h.publicState.videos = [{id: 'one', url: '/media/one'}];
  await h.context.poll();
  assert.equal(h.video.loadCalls, 1);
  h.publicState.videos = [];
  await h.context.poll();
  assert.equal(h.video.loadCalls, 2); // Reset the previously playing media once
  h.context.changeVideo();
  assert.equal(h.video.loadCalls, 2);
  h.publicState.videos = [{id: 'two', url: '/media/two'}];
  await h.context.poll();
  assert.equal(h.video.loadCalls, 3);
  assert.equal(h.video.pending.length, 2);
});

test('old play promise rejects do not blacklist a new video', async () => {
  const h = await ready();
  h.publicState.videos = [{id: 'old', url: '/media/old'}];
  await h.context.poll();
  h.publicState.videos = [{id: 'new', url: '/media/new'}];
  await h.context.poll();
  h.video.pending[0].reject(new Error('Old request aborted'));
  await settle();
  assert.equal(h.errors.length, 0);
  assert.equal(h.timers.filter(t => t.ms === 4000).length, 0);
  assert.equal(vm.runInContext('rejectedUntil.size', h.context), 0);
});

test('both OSD blocks alternate sides when a video starts playing', async () => {
  const h = await ready();
  h.publicState.videos = [{id: 'one', url: '/media/one'}, {id: 'two', url: '/media/two'}];
  await h.context.poll();
  const isRight = id => h.elements.get(id).className.includes('osd-right');
  for (const [index, right] of [false, true, false].entries()) {
    h.video.pending[index].resolve();
    await settle();
    await h.context.poll();
    assert.equal(isRight('clockCard'), right);
    assert.equal(isRight('clipFooter'), right);
    assert.equal(isRight('calendarCard'), !right);
    if (index < 2) h.video.dispatch('ended');
  }
});

test('calendar shows leading dates, holidays and transferred days off', async () => {
  const h = await ready();
  h.context.drawCalendar(new Date('2027-02-22T12:00:00Z'), 'UTC');
  let grid = h.elements.get('monthGrid').innerHTML;
  assert.match(grid, /class=""[^>]*>20</);
  assert.match(grid, /class="weekend" aria-current="date">22</);
  assert.match(grid, /class="holiday">23</);
  h.context.drawCalendar(new Date('2027-05-03T12:00:00Z'), 'UTC');
  grid = h.elements.get('monthGrid').innerHTML;
  assert.match(grid, /class="previous-month">26<\/span>.*class="previous-month">30<\/span><span class="holiday">1</);
  assert.match(grid, /class="weekend" aria-current="date">3</);
});
