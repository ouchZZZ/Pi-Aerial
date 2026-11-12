'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const displayJS = fs.readFileSync(path.join(root, 'web/display.js'), 'utf8');
const displayCSS = fs.readFileSync(path.join(root, 'web/display.css'), 'utf8');

function makeHarness(search = '') {
  const elements = new Map();
  const timers = [];
  const intervals = [];
  const errors = [];
  const reports = [];
  function element(id) {
    if (!elements.has(id)) {
      let content = '';
      const listeners = new Map();
      const item = {
        id, style: {
          setProperty(name, value) { this[name] = value; },
          removeProperty(name) { delete this[name]; },
        }, hidden: false, className: '', writes: 0,
        classList: { add() {}, remove() {} },
        addEventListener(name, callback) { listeners.set(name, callback); },
        dispatch(name) { listeners.get(name)?.(); },
        replaceChildren(...children) { this.children=children; },
        appendChild(child) { this.children??=[]; this.children.push(child); },
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
  element('externalLayer').hidden = true; // The actual display markup starts hidden.
  const video = element('video');
  video._hasSrc = false;
  video.loadCalls = 0;
  video.pauseCalls = 0;
  video.pending = [];
  video.error = null;
  video.muted = true; // <video muted> in the real display HTML.
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
  const bodyClasses = new Set();
  const session = new Map();
  const context = {
    document: {
      getElementById: element, createElement: tag => tag==='iframe' ? element('created-iframe') : element('module'),
      body: {classList: {add: name => bodyClasses.add(name), remove: name => bodyClasses.delete(name)}},
    },
    sessionStorage: {getItem: key => session.get(key), setItem: (key, value) => session.set(key, value)},
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
    fetch: async (url,options={}) => {
      if(url==='/api/display/play-once/report'){
        reports.push(JSON.parse(options.body));
        return {ok:true,json:async()=>({ok:true})};
      }
      return {ok:true,json:async()=>url==='/api/display/play-once'
        ? publicState.displayRequest : ({
        ...publicState,settings:{...publicState.settings},
        videos:[...publicState.videos],modules:[...publicState.modules],
      })};
    },
    console: {error: (...items) => errors.push(items), warn() {}},
    location: {reload() {}, search, origin: 'http://pi.local'},
  };
  vm.createContext(context);
  vm.runInContext(displayJS, context, {filename: 'display.js'});
  function finishFade(){
    const timer=timers.find(t=>t.ms===400&&!t.cleared);
    if(timer){timer.cleared=true;timer.fn();}
  }
  return {context, elements, video, timers, intervals, errors, reports, publicState, bodyClasses, finishFade};
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

test('layout preview updates OSD but never loads video or starts player polling', async () => {
  const h = makeHarness('?layout_preview=1');
  await settle();
  assert.ok(h.bodyClasses.has('layout-preview'));
  assert.ok(h.intervals.some(timer => timer.ms === 10000));
  assert.ok(!h.intervals.some(timer => timer.ms === 30000 || timer.ms === 3000));
  h.publicState.videos = [{url: '/media/expensive-video.mp4'}];
  await h.context.poll();
  assert.equal(h.video.loadCalls, 0);
});

test('night shade covers all OSD layers and remains separate from one-shot hiding', () => {
  const zIndex = selector => Number(displayCSS.match(new RegExp(selector + '\\{[^}]*z-index:(\\d+)'))[1]);
  assert.ok(zIndex('#nightShade') > zIndex('\\.module-layer'));
  assert.ok(zIndex('#nightShade') > zIndex('\\.calendar-card'));
  assert.match(displayCSS, /body\.playing-once :is\(#shade,#clockCard,#weatherCard,#calendarCard,#moduleLayer,#clipFooter\)\{display:none\}/);
});

test('one-shot skips the rotation timer and restores OSD and playlist on end or failure', async () => {
  const h = await ready();
  h.publicState.settings.video_interval_minutes = 1;
  h.publicState.videos = [{url: '/media/first.mp4'}];
  await h.context.poll();
  const rotation = h.timers.at(-1);
  h.publicState.displayRequest = {id: 'once', url: '/api/display/play-once/once'};
  await h.context.pollDisplayRequest();
  assert.equal(h.video.src, h.publicState.displayRequest.url);
  assert.equal(h.video.muted, false);
  assert.ok(h.bodyClasses.has('playing-once'));
  assert.ok(rotation.cleared);
  h.video.pending.at(-1).resolve();
  await settle();
  assert.ok(h.timers.every(timer => timer.cleared));

  h.publicState.videos.push({url: '/media/second.mp4'});
  await h.context.poll();
  assert.equal(h.video.src, h.publicState.displayRequest.url);
  h.video.dispatch('ended'); h.finishFade();
  assert.equal(h.video.src, '/media/second.mp4');
  assert.equal(h.video.muted, true);
  assert.ok(!h.bodyClasses.has('playing-once'));
  await h.context.pollDisplayRequest();
  assert.equal(h.video.src, '/media/second.mp4');

  h.publicState.displayRequest = {id: 'broken', url: '/api/display/play-once/broken'};
  await h.context.pollDisplayRequest();
  h.video.pending.at(-1).reject(new Error('Format unsupported'));
  await settle();
  assert.equal(h.video.src, '/media/first.mp4');
  assert.equal(h.video.muted, true);
  assert.ok(!h.bodyClasses.has('playing-once'));
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

test('stop cancels an active one-shot and leaves regular rotation alone', async () => {
  const h = await ready();
  h.publicState.videos = [{url: '/media/normal.mp4'}];
  await h.context.poll();
  h.publicState.displayRequest = {id: 'stop-test', url: '/api/display/play-once/stop-test'};
  await h.context.pollDisplayRequest();
  const oneShot = h.video.pending.at(-1);
  assert.ok(h.bodyClasses.has('playing-once'));

  h.publicState.displayRequest = null;
  await h.context.pollDisplayRequest();
  assert.ok(h.bodyClasses.has('playing-once'));

  h.publicState.displayRequest = {stop: true};
  await h.context.pollDisplayRequest();
  assert.equal(h.video.src, '/media/normal.mp4');
  assert.equal(h.video.muted, true);
  assert.ok(!h.bodyClasses.has('playing-once'));
  const loadCalls = h.video.loadCalls;
  oneShot.reject(new Error('Playback interrupted'));
  await settle();
  await h.context.pollDisplayRequest();
  assert.equal(h.video.loadCalls, loadCalls);
  assert.equal(h.errors.length, 0);
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
    if (index < 2) {h.video.dispatch('ended'); h.finishFade();}
  }
});

test('single-position OSD layout remains fixed when videos change', async () => {
  const h = await ready();
  const layout = {
    clock: {x: 12, y: 8, scale: 1.2},
    weather: {x: 50, y: 75, scale: 0.8},
    calendar: {x: 70, y: 7, scale: 1},
    footer: {x: 9, y: 92, scale: 0.7},
  };
  h.publicState.settings.osd_layout_mode = 'single';
  h.publicState.settings.osd_layout = layout;
  await h.context.poll();
  assert.ok(h.bodyClasses.has('custom-osd-layout'));
  assert.equal(h.elements.get('clockCard').style.left, '12%');
  assert.equal(h.elements.get('weatherCard').style.top, '75%');
  assert.equal(h.elements.get('weatherCard').style.transform, 'scale(0.8)');
  assert.equal(h.elements.get('calendarCard').style.left, '70%');
  h.publicState.videos = [{url:'/media/a.mp4'},{url:'/media/b.mp4'}];
  await h.context.poll();
  h.video.pending.at(-1).resolve();
  await settle();
  h.video.dispatch('ended'); h.finishFade();
  h.video.pending.at(-1).resolve();
  await settle();
  assert.ok(!h.elements.get('clockCard').className.includes('osd-right'));
  assert.ok(!h.elements.get('weatherCard').className.includes('osd-right'));
  h.publicState.settings.osd_layout = {};
  await h.context.poll();
  assert.ok(!h.bodyClasses.has('custom-osd-layout'));
  assert.equal(h.elements.get('weatherCard').style.left, '');
});

test('alternating custom OSD layouts cycle on every successful video start', async () => {
  const h=await ready();
  const first={
    clock:{x:8,y:10,scale:1},weather:{x:5,y:37,scale:1},
    calendar:{x:72,y:10,scale:1},footer:{x:5,y:90,scale:1},
  };
  const second={
    clock:{x:58,y:8,scale:.75},weather:{x:52,y:45,scale:1.25},
    calendar:{x:5,y:14,scale:1.1},footer:{x:35,y:93,scale:.8},
  };
  h.publicState.settings.osd_layout_mode='alternate';
  h.publicState.settings.osd_layout=first;
  h.publicState.settings.osd_layout_alt=second;
  h.publicState.videos=[
    {id:'one',url:'/media/one.mp4'},
    {id:'two',url:'/media/two.mp4'},
    {id:'three',url:'/media/three.mp4'},
  ];
  await h.context.poll();
  assert.equal(h.elements.get('clockCard').style.left,'8%');
  for(const [index,expected] of [first,second,first].entries()){
    h.video.pending[index].resolve();
    await settle();
    assert.equal(h.elements.get('clockCard').style.left,expected.clock.x+'%');
    assert.equal(h.elements.get('weatherCard').style.top,expected.weather.y+'%');
    assert.equal(h.elements.get('calendarCard').style.transform,'scale('+expected.calendar.scale+')');
    await h.context.poll();
    assert.equal(h.elements.get('clockCard').style.left,expected.clock.x+'%');
    if(index<2){h.video.dispatch('ended'); h.finishFade();}
  }
});

test('font and color follow each OSD layout without leaking between blocks', async () => {
  const h=await ready();
  const first={
    clock:{x:8,y:10,scale:1,font:'georgia',color:'#ffcc00'},
    weather:{x:5,y:37,scale:1,font:'courier',color:'#aabbcc'},
    calendar:{x:72,y:10,scale:1},footer:{x:5,y:90,scale:1},
  };
  const second={
    clock:{x:58,y:8,scale:.75,font:'arial',color:'#112233'},
    weather:{x:52,y:45,scale:1},calendar:{x:5,y:14,scale:1},
    footer:{x:35,y:93,scale:.8,font:'trebuchet',color:'#334455'},
  };
  h.publicState.settings.osd_layout_mode='alternate';
  h.publicState.settings.osd_layout=first;
  h.publicState.settings.osd_layout_alt=second;
  h.publicState.videos=[{url:'/media/a'},{url:'/media/b'}];
  await h.context.poll();
  const clock=h.elements.get('clockCard').style;
  const weather=h.elements.get('weatherCard').style;
  const footer=h.elements.get('clipFooter').style;
  assert.match(clock.fontFamily,/Georgia/);
  assert.equal(clock.color,'#ffcc00');
  assert.equal(clock['--osd-text-color'],'#ffcc00');
  assert.match(weather.fontFamily,/Courier New/);
  assert.equal(weather['--osd-text-color'],'#aabbcc');
  assert.equal(footer.color,'');
  h.video.pending[0].resolve();
  await settle();
  h.video.dispatch('ended'); h.finishFade();
  h.video.pending[1].resolve();
  await settle();
  assert.match(clock.fontFamily,/Arial/);
  assert.equal(clock.color,'#112233');
  assert.equal(weather.fontFamily,'');
  assert.equal(weather.color,'');
  assert.equal(weather['--osd-text-color'],undefined);
  assert.match(footer.fontFamily,/Trebuchet MS/);
  assert.equal(footer.color,'#334455');
  h.publicState.settings.osd_layout={};
  h.publicState.settings.osd_layout_alt={};
  await h.context.poll();
  assert.equal(clock.fontFamily,'');
  assert.equal(clock.color,'');
  assert.equal(clock['--osd-text-color'],undefined);
  assert.equal(footer.fontFamily,'');
  assert.equal(footer.color,'');
});

test('single-position mode uses the default first position without rotating', async () => {
  const h=await ready();
  h.publicState.settings.osd_layout_mode='single';
  h.publicState.videos=[{url:'/media/a'},{url:'/media/b'}];
  await h.context.poll();
  h.video.pending[0].resolve();
  await settle();
  assert.equal(h.elements.get('clockCard').className,'clock-card');
  h.video.dispatch('ended'); h.finishFade();
  h.video.pending[1].resolve();
  await settle();
  assert.equal(h.elements.get('clockCard').className,'clock-card');
  assert.equal(h.elements.get('calendarCard').className,'calendar-card osd-right');
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

test('weekly forecast skips today and follows the weather switch', async () => {
  const h = await ready();
  vm.runInContext("Date = class extends Date { constructor(...args) { super(...(args.length ? args : ['2027-01-01T12:00:00Z'])); } }", h.context);
  h.publicState.weather = {
    available: true, temperature: 1, apparent: 0, code: 0, wind: 18, is_day: true,
    forecast: Array.from({length: 9}, (_, i) => ({
      date: '2027-01-0' + (i + 1), min: -i, max: i, code: 0,
    })),
  };
  await h.context.poll();
  const forecast = h.elements.get('forecast');
  assert.equal(forecast.hidden, false);
  assert.equal((forecast.innerHTML.match(/class="forecast-day"/g) || []).length, 7);
  assert.doesNotMatch(forecast.innerHTML, />0°</);
  assert.match(h.elements.get('wxDetail').textContent, /5 м\/с/);
  h.publicState.settings.show_weather = false;
  await h.context.poll();
  assert.equal(forecast.hidden, true);
});

test('calendar fills the last week with muted dates from the next month', async () => {
  const h = await ready();
  h.context.drawCalendar(new Date('2027-12-31T12:00:00Z'), 'UTC');
  assert.match(h.elements.get('monthGrid').innerHTML, /<span class="next-month">1<\/span><span class="next-month">2<\/span>$/);
});


test('English UI localizes Moscow, date, weekly forecast and weather details', async () => {
  const h = await ready();
  h.publicState.settings.language = 'en';
  h.publicState.settings.timezone = 'Europe/Moscow';
  h.publicState.settings.city = 'Москва';
  h.publicState.weather = {
    available: true, temperature: 18, apparent: 17, code: 0,
    wind: 18, is_day: true, stale: false,
    forecast: [{date: '2027-01-11', min: 9, max: 20, code: 0}],
  };
  await h.context.poll();
  assert.equal(h.elements.get('city').textContent, 'Moscow');
  assert.equal(h.elements.get('weatherCredit').textContent, 'Weather: Open-Meteo');
  assert.match(h.elements.get('wxTemp').textContent, /Clear/);
  assert.match(h.elements.get('wxDetail').textContent, /Feels like 17°/);
  assert.match(h.elements.get('wxDetail').textContent, /5 m\/s/);
  assert.match(h.elements.get('monthGrid').innerHTML, /Mon/);
  assert.doesNotMatch(h.elements.get('monthGrid').innerHTML, /Пн/);

  h.publicState.settings.language = 'ru';
  await h.context.poll();
  assert.equal(h.elements.get('city').textContent, 'Москва');
  assert.equal(h.elements.get('weatherCredit').textContent, 'Погода: Open-Meteo');
  assert.match(h.elements.get('wxTemp').textContent, /Ясно/);
  assert.match(h.elements.get('wxDetail').textContent, /Ощущается/);
  assert.match(h.elements.get('monthGrid').innerHTML, /Пн/);
});

test('source and OSD swap under black after fade-out and reveal on first frame', async()=>{
  const h=await ready();
  let present;
  h.video.requestVideoFrameCallback=callback=>{present=callback;return 1;};
  h.video.cancelVideoFrameCallback=()=>{};
  h.publicState.videos=[
    {url:'/media/a',description:'Old caption'},
    {url:'/media/b',description:'New caption'},
  ];
  await h.context.poll();
  h.video.pending[0].resolve();
  await settle();
  assert.ok(h.bodyClasses.has('media-transition'));
  present();
  assert.ok(!h.bodyClasses.has('media-transition'));
  assert.equal(h.elements.get('clipDescription').textContent,'Old caption');
  h.video.dispatch('ended');
  assert.ok(h.bodyClasses.has('media-transition'));
  assert.equal(h.video.src,'/media/a');
  assert.equal(h.elements.get('clipDescription').textContent,'Old caption');
  h.finishFade();
  assert.equal(h.video.src,'/media/b');
  assert.equal(h.elements.get('clipDescription').textContent,'New caption');
  h.video.pending[1].resolve();
  await settle();
  assert.ok(h.bodyClasses.has('media-transition'));
  assert.ok(!h.elements.get('clockCard').className.includes('osd-right'));
  present();
  assert.ok(!h.bodyClasses.has('media-transition'));
  assert.ok(h.elements.get('clockCard').className.includes('osd-right'));
});

test('empty playlist cancels pending source switch and restores idle OSD', async()=>{
  const h=await ready();
  h.publicState.videos=[{url:'/media/a'},{url:'/media/b'}];
  await h.context.poll();
  h.video.pending[0].resolve();
  await settle();
  h.video.dispatch('ended');
  assert.ok(h.bodyClasses.has('media-transition'));
  const loads=h.video.loadCalls;
  h.publicState.videos=[];
  await h.context.poll();
  h.finishFade();
  assert.equal(h.video.loadCalls,loads+1);
  assert.ok(!h.bodyClasses.has('media-transition'));
  assert.ok(!h.video.hasAttribute('src'));
});

test('stale first-frame callback cannot reveal or rotate a newer attempt', async()=>{
  const h=await ready();
  const frames=[];
  h.video.requestVideoFrameCallback=callback=>{frames.push(callback);return frames.length;};
  h.video.cancelVideoFrameCallback=()=>{};
  h.publicState.videos=[{url:'/media/a'}];
  await h.context.poll();
  h.video.pending[0].resolve();
  await settle();
  h.publicState.videos=[{url:'/media/b'}];
  await h.context.poll();
  frames[0]();
  assert.ok(h.bodyClasses.has('media-transition'));
  h.video.pending[1].resolve();
  await settle();
  frames[1]();
  assert.ok(!h.bodyClasses.has('media-transition'));
  assert.ok(!h.elements.get('clockCard').className.includes('osd-right'));
});

test('video startup timeout releases black curtain and schedules fallback', async()=>{
  const h=await ready();
  h.publicState.videos=[{url:'/media/a'}];
  await h.context.poll();
  h.timers.find(t=>t.ms===15000&&!t.cleared).fn();
  assert.ok(!h.bodyClasses.has('media-transition'));
  assert.equal(h.timers.filter(t=>t.ms===4000).length,1);
});

test('one-shot preempts a pending fade and cancels its delayed playlist load', async()=>{
  const h=await ready();
  h.publicState.videos=[{url:'/media/a'},{url:'/media/b'}];
  await h.context.poll();
  h.video.pending[0].resolve();
  await settle();
  h.video.dispatch('ended');
  h.publicState.displayRequest={id:'interrupt',url:'/api/display/play-once/interrupt'};
  await h.context.pollDisplayRequest();
  h.finishFade();
  assert.equal(h.video.src,'/api/display/play-once/interrupt');
  h.video.pending.at(-1).resolve();
  await settle();
  assert.ok(h.bodyClasses.has('playing-once'));
  assert.ok(!h.bodyClasses.has('media-transition'));
});

test('rotation interval starts after first frame rather than while loading', async()=>{
  const h=await ready();
  let present;
  h.video.requestVideoFrameCallback=callback=>{present=callback;return 1;};
  h.publicState.settings.video_interval_minutes=1;
  h.publicState.videos=[{url:'/media/a'}];
  await h.context.poll();
  assert.equal(h.timers.filter(t=>t.ms===60000&&!t.cleared).length,0);
  h.video.pending[0].resolve();
  await settle();
  assert.equal(h.timers.filter(t=>t.ms===60000&&!t.cleared).length,0);
  present();
  assert.equal(h.timers.filter(t=>t.ms===60000&&!t.cleared).length,1);
});


test('screensaver stays muted on rotation; manual play-once is audible until stopped', async()=>{
  const h=await ready();
  h.publicState.videos=[
    {id:'a',url:'/media/a.mp4'},
    {id:'b',url:'/media/b.mp4'},
  ];
  await h.context.poll();
  assert.equal(h.video.muted,true);
  h.video.pending[0].resolve();
  await settle();

  h.video.dispatch('ended');
  h.finishFade();
  assert.equal(h.video.src,'/media/b.mp4');
  assert.equal(h.video.muted,true);

  h.publicState.displayRequest={id:'manual',url:'/api/display/play-once/manual'};
  await h.context.pollDisplayRequest();
  assert.equal(h.video.src,'/api/display/play-once/manual');
  assert.equal(h.video.muted,false);
  assert.equal(h.bodyClasses.has('playing-once'),true);

  h.publicState.displayRequest={stop:true};
  await h.context.pollDisplayRequest();
  assert.equal(h.video.muted,true);
  assert.equal(h.video.src,'/media/a.mp4');
  assert.equal(h.bodyClasses.has('playing-once'),false);
});

test('manual play-once failure restores silence even with no available playlist', async()=>{
  const h=await ready();
  h.publicState.displayRequest={id:'only-once',url:'/api/display/play-once/only-once'};
  await h.context.pollDisplayRequest();
  assert.equal(h.video.muted,false);

  h.video.pending.at(-1).reject(new Error('Video unavailable'));
  await settle();
  assert.equal(h.video.muted,true);
  assert.equal(h.bodyClasses.has('playing-once'),false);
});

test('YouTube one-shot uses its own player, interrupts rotation, and restores on end', async()=>{
  const h=await ready();
  const players=[];
  class FakePlayer{
    constructor(element,options){this.element=element;this.options=options;players.push(this);}
    destroy(){this.destroyed=true;}
    unMute(){this.unmuted=true;}
    playVideo(){this.started=true;}
  }
  h.context.YT={Player:FakePlayer,PlayerState:{PLAYING:1,ENDED:0}};
  h.publicState.videos=[{url:'/media/background.mp4'}];
  await h.context.poll();
  const pending=h.video.pending.at(-1);
  h.publicState.displayRequest={
    id:'youtube-one',kind:'embed',provider:'youtube',video_id:'dQw4w9WgXcQ'
  };
  await h.context.pollDisplayRequest();
  await settle();
  assert.equal(players.length,1);
  assert.equal(players[0].element,'externalPlayer');
  assert.equal(players[0].options.videoId,'dQw4w9WgXcQ');
  assert.equal(players[0].options.playerVars.origin,'http://pi.local');
  assert.equal(h.elements.get('externalLayer').hidden,false);
  assert.ok(h.bodyClasses.has('playing-once'));
  assert.ok(h.video.pauseCalls>0);
  pending.reject(new Error('Interrupted by YouTube'));
  await settle();
  assert.ok(h.bodyClasses.has('playing-once'));
  players[0].options.events.onReady({target:players[0]});
  assert.equal(players[0].unmuted,true);
  assert.equal(players[0].started,true);
  players[0].options.events.onStateChange({data:1});
  players[0].options.events.onStateChange({data:0});
  assert.ok(players[0].destroyed);
  assert.equal(h.elements.get('externalLayer').hidden,true);
  assert.equal(h.video.src,'/media/background.mp4');
  assert.equal(h.video.muted,true);
  assert.ok(!h.bodyClasses.has('playing-once'));
});

test('Stop and YouTube playback errors discard stale iframe callbacks', async()=>{
  const h=await ready();
  const players=[];
  class FakePlayer{
    constructor(element,options){this.options=options;players.push(this);}
    destroy(){this.destroyed=true;}
  }
  h.context.YT={Player:FakePlayer,PlayerState:{PLAYING:1,ENDED:0}};
  h.publicState.videos=[{url:'/media/rotation.mp4'}];
  await h.context.poll();
  h.publicState.displayRequest={id:'yt-stop',kind:'embed',provider:'youtube',video_id:'dQw4w9WgXcQ'};
  await h.context.pollDisplayRequest();
  await settle();
  h.publicState.displayRequest={stop:true};
  await h.context.pollDisplayRequest();
  assert.equal(players[0].destroyed,true);
  assert.equal(h.video.src,'/media/rotation.mp4');
  players[0].options.events.onError({data:150});
  assert.ok(!h.bodyClasses.has('playing-once'));
  assert.equal(h.errors.length,0);

  h.publicState.displayRequest={id:'yt-error',kind:'embed',provider:'youtube',video_id:'dQw4w9WgXcQ'};
  await h.context.pollDisplayRequest();
  await settle();
  assert.equal(players.length,2);
  players[1].options.events.onError({data:150});
  assert.equal(h.video.src,'/media/rotation.mp4');
  assert.equal(h.video.muted,true);
  assert.ok(!h.bodyClasses.has('playing-once'));
  assert.equal(h.elements.get('externalLayer').hidden,true);
});

test('Malformed external video descriptors cannot inject a player URL', async()=>{
  const h=await ready();
  h.publicState.videos=[{url:'/media/rotation.mp4'}];
  await h.context.poll();
  h.publicState.displayRequest={
    id:'yt-malformed',kind:'embed',provider:'youtube',video_id:'../javascript'
  };
  await h.context.pollDisplayRequest();
  assert.equal(h.elements.get('externalLayer').hidden,true);
  assert.equal(h.video.src,'/media/rotation.mp4');
  assert.equal(h.bodyClasses.has('playing-once'),false);
});

test('VK Video one-shot embeds validated URL and resumes silent rotation on end', async()=>{
  const h=await ready();
  const players=[];
  const VK={VideoPlayer:iframe=>{
    const handlers=new Map();
    const player={
      iframe,handlers,
      on(name,listener){handlers.set(name,listener);},
      destroy(){this.destroyed=true;},
      unmute(){this.unmuted=true;},
      play(){this.played=true;}
    };
    players.push(player);
    return player;
  }};
  h.context.VK=VK;
  h.publicState.videos=[{url:'/media/background.mp4'}];
  await h.context.poll();
  const pending=h.video.pending.at(-1);
  h.publicState.displayRequest={
    id:'vk-one',kind:'embed',provider:'vkvideo',
    owner_id:'-219483134',video_id:'456253880',hash:'abc123def'
  };
  await h.context.pollDisplayRequest();
  await settle();
  assert.equal(players.length,1);
  const frame=players[0].iframe;
  assert.equal(frame.id,'externalVKPlayer');
  assert.match(frame.src,/^https:\/\/vkvideo\.ru\/video_ext\.php\?/);
  assert.match(frame.src,/oid=-219483134/);
  assert.match(frame.src,/id=456253880/);
  assert.match(frame.src,/hash=abc123def/);
  assert.match(frame.src,/hd=4/);
  assert.match(frame.src,/js_api=1/);
  assert.match(frame.allow,/autoplay/);
  assert.equal(h.elements.get('externalLayer').hidden,false);
  assert.ok(h.bodyClasses.has('playing-once'));
  assert.ok(h.video.pauseCalls>0);
  pending.reject(new Error('Interrupted by VK'));
  await settle();
  assert.ok(h.bodyClasses.has('playing-once'));
  players[0].handlers.get('inited')();
  assert.equal(players[0].unmuted,true);
  assert.equal(players[0].played,true);
  players[0].handlers.get('started')();
  assert.ok(h.timers.filter(t=>t.ms===45000).every(t=>t.cleared));
  players[0].handlers.get('ended')();
  assert.equal(players[0].destroyed,true);
  assert.equal(h.elements.get('externalLayer').hidden,true);
  assert.equal(h.video.src,'/media/background.mp4');
  assert.equal(h.video.muted,true);
  assert.ok(!h.bodyClasses.has('playing-once'));
});

test('VK stop, error and late events cannot disrupt resumed screensaver', async()=>{
  const h=await ready();
  const players=[];
  h.context.VK={VideoPlayer:iframe=>{
    const handlers=new Map();
    const player={
      iframe,handlers,
      on(name,listener){handlers.set(name,listener);},
      destroy(){this.destroyed=true;},
      unmute(){},play(){}
    };
    players.push(player);return player;
  }};
  h.publicState.videos=[{url:'/media/rotation.mp4'}];
  await h.context.poll();
  h.publicState.displayRequest={
    id:'vk-stop',kind:'embed',provider:'vkvideo',owner_id:'7',video_id:'123'
  };
  await h.context.pollDisplayRequest();
  await settle();
  assert.ok(!players[0].iframe.src.includes('hash='));
  h.publicState.displayRequest={stop:true};
  await h.context.pollDisplayRequest();
  assert.equal(players[0].destroyed,true);
  assert.equal(h.video.src,'/media/rotation.mp4');
  players[0].handlers.get('ended')();
  players[0].handlers.get('error')({state:'late error'});
  assert.equal(h.errors.length,0);
  assert.equal(h.bodyClasses.has('playing-once'),false);
  h.publicState.displayRequest={
    id:'vk-error',kind:'embed',provider:'vkvideo',owner_id:'7',video_id:'123'
  };
  await h.context.pollDisplayRequest();
  await settle();
  assert.equal(players.length,2);
  players[1].handlers.get('error')({state:'unavailable'});
  assert.equal(h.video.src,'/media/rotation.mp4');
  assert.equal(h.video.muted,true);
  assert.equal(h.bodyClasses.has('playing-once'),false);
  assert.equal(h.elements.get('externalLayer').hidden,true);
});

test('VK iframe startup timeout and external script errors restore regular playback', async()=>{
  const h=await ready();
  h.context.VK={VideoPlayer:iframe=>({
    on(){},destroy(){},unmute(){},play(){}
  })};
  h.publicState.videos=[{url:'/media/rotation.mp4'}];
  await h.context.poll();
  h.publicState.displayRequest={
    id:'vk-timeout',kind:'embed',provider:'vkvideo',owner_id:'-23',video_id:'99'
  };
  await h.context.pollDisplayRequest();
  await settle();
  const timer=h.timers.find(t=>t.ms===45000&&!t.cleared);
  assert.ok(timer);
  timer.fn();
  assert.equal(h.elements.get('externalLayer').hidden,true);
  assert.equal(h.video.src,'/media/rotation.mp4');
  assert.equal(h.bodyClasses.has('playing-once'),false);

  // A malformed descriptor cannot turn VK's fixed iframe into an arbitrary URL.
  h.publicState.displayRequest={
    id:'vk-inject',kind:'embed',provider:'vkvideo',
    owner_id:'-23&src=evil',video_id:'99'
  };
  await h.context.pollDisplayRequest();
  assert.equal(h.elements.get('externalLayer').hidden,true);
  assert.equal(h.bodyClasses.has('playing-once'),false);
});

test('YouTube embed reports useful status and error 153 without leaking a URL', async()=>{
  const h=await ready();
  const players=[];
  class FakePlayer{
    constructor(element,options){this.options=options;players.push(this);}
    destroy(){this.destroyed=true;}
  }
  h.context.YT={Player:FakePlayer,PlayerState:{PLAYING:1,ENDED:0}};
  h.publicState.videos=[{url:'/media/background.mp4'}];
  await h.context.poll();
  h.publicState.displayRequest={
    id:'youtube-report',kind:'embed',provider:'youtube',video_id:'dQw4w9WgXcQ'
  };
  await h.context.pollDisplayRequest();
  await settle();
  assert.deepEqual(h.reports.map(report=>report.stage),['starting','api_ready']);
  players[0].options.events.onError({data:153});
  await settle();
  assert.deepEqual(h.reports.map(report=>report.stage),['starting','api_ready','error']);
  assert.equal(h.reports[2].detail,'YouTube error 153');
  assert.equal(h.reports[2].id,'youtube-report');
  assert.ok(!('url' in h.reports[2]));
  assert.equal(h.video.src,'/media/background.mp4');
  assert.equal(h.bodyClasses.has('playing-once'),false);
});
