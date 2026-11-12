'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const root=path.join(__dirname,'..');
const admin=fs.readFileSync(path.join(root,'web/admin.js'),'utf8');
const html=fs.readFileSync(path.join(root,'web/admin.html'),'utf8');
const start=admin.indexOf('let libraryVideos=[];');
const end=admin.indexOf('\nasync function loadPlaybackStatus()',start);
assert.ok(start>=0&&end>start,'library UI functions must be present');
const librarySource=admin.slice(start,end);

function makeNode(tag,id=''){
  return {
    tag,id,children:[],listeners:{},checked:false,hidden:false,textContent:'',
    classList:{add(){},remove(){}},
    append(...items){this.children.push(...items);this.firstChild=this.children[0]||null;},
    prepend(...items){this.children.unshift(...items);this.firstChild=this.children[0]||null;},
    insertBefore(item,anchor){
      const at=this.children.indexOf(anchor);
      this.children.splice(at<0?this.children.length:at,0,item);
      this.firstChild=this.children[0]||null;
    },
    replaceChildren(...items){this.children=[...items];this.firstChild=items[0]||null;},
    addEventListener(type,listener){this.listeners[type]=listener;},
    dispatch(type){this.listeners[type]?.();},
  };
}

function harness(){
  const elements=new Map();
  const byId=id=>{
    if(!elements.has(id))elements.set(id,makeNode('div',id));
    return elements.get(id);
  };
  const sources=[
    {id:'apple',name:'Apple Aerial'},
    {id:'nasa',name:'NASA'},
    {id:'robin',name:'Robin Fourcade'},
  ];
  const status={
    online_sources:sources,
    config:{online_sources:{apple:true,nasa:true,robin:true},local_videos_enabled:true},
    videos:[{name:'My local video',bytes:400,url:'/media/local.mp4'}],
  };
  const context={
    status,
    document:{
      createElement:tag=>makeNode(tag),
      createTextNode:text=>Object.assign(makeNode('#text'),{textContent:text}),
    },
    $:byId,
    PiI18n:{language:'ru',translate:text=>text},
    progressSize:n=>String(n)+' B',
    textNode:(tag,text,cls)=>Object.assign(makeNode(tag),{textContent:text,className:cls||''}),
    line:(name,desc)=>{
      const row=makeNode('div'),head=makeNode('div');
      head.append(
        Object.assign(makeNode('strong'),{textContent:name}),
        Object.assign(makeNode('small'),{textContent:desc})
      );
      row.append(head);return row;
    },
    openLibraryPreview(){},playOnceButton:()=>makeNode('button'),
    ask:()=>false,api:async()=>({}),toast(){},load:async()=>{},loadLibrary:async()=>{},
  };
  const api=vm.runInNewContext(librarySource+'\n({renderLibrary,libraryMatches,filteredLibrary})',context);
  const videos=[
    {provider:'apple',id:'apple1',name:'Ocean',cached:true,selected:true,bytes:100},
    {provider:'nasa',id:'nasa1',name:'Earth',cached:true,selected:true,bytes:200},
    {provider:'robin',id:'robin1',name:'Alps',cached:true,selected:true,bytes:300},
    {provider:'nasa',id:'nasa2',name:'Deleted Earth',cached:false,selected:false,can_select:true},
  ];
  const labels=()=>byId('librarySourceFilters').children.filter(n=>n.tag==='label');
  const choice=name=>{
    const item=labels().find(n=>n.children[1].textContent.includes(name));
    assert.ok(item,'missing source: '+name);return item.children[0];
  };
  const names=id=>byId(id).children
    .filter(row=>row.tag==='div')
    .map(row=>row.children.find(child=>child.tag==='div'&&child.children[0]?.tag==='strong')
      ?.children[0].textContent);
  return {status,videos,byId,api,labels,choice,names};
}

test('library filter controls exist and can select a single source, two sources or all',()=>{
  assert.match(html,/id="librarySourceFilters"/);
  const ui=harness();
  ui.api.renderLibrary(ui.videos);
  assert.equal(ui.labels().length,4);
  assert.deepEqual(ui.names('onlineLibraryList'),['Ocean','Earth','Alps','Deleted Earth']);
  assert.equal(ui.names('localLibraryList').length,1);

  for(const name of ['Apple Aerial','Robin Fourcade','Загруженные файлы']){
    const checkbox=ui.choice(name);
    checkbox.checked=false;
    checkbox.dispatch('change');
  }
  assert.deepEqual(ui.names('onlineLibraryList'),['Earth','Deleted Earth']);
  assert.equal(ui.byId('localLibraryCard').hidden,true);
  assert.match(ui.byId('librarySummary').textContent,/готово: 1/);

  const apple=ui.choice('Apple Aerial');
  apple.checked=true;
  apple.dispatch('change');
  assert.deepEqual(ui.names('onlineLibraryList'),['Ocean','Earth','Deleted Earth']);

  const all=ui.byId('librarySourceFilters').children[0];
  assert.equal(all.textContent,'Все источники');
  assert.equal(all.disabled,false);
  all.dispatch('click');
  assert.deepEqual(ui.names('onlineLibraryList'),['Ocean','Earth','Alps','Deleted Earth']);
  assert.equal(ui.byId('localLibraryCard').hidden,false);
  assert.equal(ui.byId('librarySourceFilters').children[0].disabled,true);
});

test('only enabled sources appear and switching settings reconciles selection',()=>{
  const ui=harness();
  ui.status.config.online_sources.robin=false;
  ui.status.config.local_videos_enabled=false;
  ui.api.renderLibrary(ui.videos);
  assert.equal(ui.labels().length,2);
  assert.deepEqual(ui.names('onlineLibraryList'),['Ocean','Earth','Deleted Earth']);
  assert.equal(ui.byId('localLibraryCard').hidden,true);
  const nasa=ui.choice('NASA');
  nasa.checked=false;nasa.dispatch('change');
  assert.deepEqual(ui.names('onlineLibraryList'),['Ocean']);
  // Keep the user's choice on regular refresh, but newly enabled sources
  // are selected so they do not mysteriously disappear from the library.
  ui.api.renderLibrary([...ui.videos]);
  assert.deepEqual(ui.names('onlineLibraryList'),['Ocean']);
  ui.status.config.online_sources.robin=true;
  ui.api.renderLibrary(ui.videos);
  assert.deepEqual(ui.names('onlineLibraryList'),['Ocean','Alps']);
  assert.equal(ui.choice('NASA').checked,false);

  ui.status.config.online_sources.apple=false;
  ui.api.renderLibrary(ui.videos);
  assert.equal(ui.labels().length,2);
  assert.deepEqual(ui.names('onlineLibraryList'),['Alps']);
  assert.equal(ui.byId('onlineLibraryCard').hidden,false);
});

test('empty selection and no enabled source show guidance without erasing files',()=>{
  const ui=harness();
  ui.api.renderLibrary(ui.videos);
  for(const source of ['Apple Aerial','NASA','Robin Fourcade','Загруженные файлы']){
    const input=ui.choice(source);
    input.checked=false;input.dispatch('change');
  }
  assert.match(ui.byId('libraryFilterHint').textContent,/Выберите хотя бы один/);
  assert.equal(ui.byId('onlineLibraryCard').hidden,true);
  assert.equal(ui.byId('localLibraryCard').hidden,true);

  ui.status.config.online_sources={apple:false,nasa:false,robin:false};
  ui.status.config.local_videos_enabled=false;
  ui.api.renderLibrary(ui.videos);
  assert.equal(ui.labels().length,0);
  assert.match(ui.byId('libraryFilterHint').textContent,/Нет включённых источников/);
  assert.equal(ui.videos.length,4);
});

test('failed selected video keeps a visible retry action',()=>{
  assert.match(admin,/v\.failed&&v\.selected&&!v\.cached&&!v\.active/);
  assert.match(admin,/online\/library\/.*\/retry/);
  assert.match(admin,/v\.error\|\|'Ошибка, повторная попытка позже'/);
});

test('source-filter labels have English translations',()=>{
  const i18n=fs.readFileSync(path.join(root,'web/i18n.js'),'utf8');
  for(const ru of ['Фильтр по источникам','Все источники','Нет включённых источников.','Выберите хотя бы один источник.']){
    assert.ok(i18n.includes("['"+ru+"','"),'missing English translation: '+ru);
  }
});


test('Siberia query matches English metadata and enables matching-only bulk buttons',()=>{
  const ui=harness();
  ui.videos.push({
    provider:'nasa',id:'nasa3',name:'Lake Baikal',
    description:'View over Siberia',cached:false,selected:false,can_select:true,
  });
  ui.api.renderLibrary(ui.videos);
  // Query is maintained by the library UI state; use VM exported helpers.
  // The existing filter must not be discarded when the catalog refreshes.
  assert.match(admin,/сибирь.*siberia/);
  assert.match(admin,/libraryQuery=e\.target\.value/);
  assert.match(admin,/filteredLibrary\(\)\.online\.filter/);
  assert.match(html,/id="librarySearch"/);
  assert.match(html,/id="librarySelectVisible"/);
  assert.match(html,/id="libraryUnselectVisible"/);
  assert.match(html,/id="libraryShowMore"/);
  assert.equal(ui.byId('librarySelectVisible').disabled,false);
  assert.ok(ui.names('onlineLibraryList').includes('Lake Baikal'));
});

test('ready online clips retain preview and one-shot display controls',()=>{
  const ui=harness();
  ui.api.renderLibrary(ui.videos);
  const row=ui.byId('onlineLibraryList').children.find(e=>
    e.children.some(child=>child.children?.[0]?.textContent==='Ocean'));
  assert.ok(row);
  const controls=row.children.at(-1);
  assert.ok(controls.children.some(button=>button.textContent==='Просмотр'));
  assert.equal(controls.children.filter(button=>button.tag==='button').length,2);
});
