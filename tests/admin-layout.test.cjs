'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../web/admin-layout.js'),'utf8');

function mockElement(id){
  const listeners=new Map();
  const classes=new Set();
  return {
    id,style:{},value:'',hidden:false,disabled:false,children:[],className:'',
    classList:{toggle:(name,on)=>on?classes.add(name):classes.delete(name),
      add:name=>classes.add(name),remove:name=>classes.delete(name),
      contains:name=>classes.has(name)},
    setAttribute(){},
    getAttribute(name){return name==='src'?(this.src||null):null;},
    append(...children){this.children.push(...children);},
    addEventListener(name,fn){listeners.set(name,fn);},
    dispatch(name,event={}){return listeners.get(name)?.(event);},
    getBoundingClientRect(){return {width:640,height:360,left:0,top:0};},
    setPointerCapture(){},hasPointerCapture(){return false;},releasePointerCapture(){},
  };
}

function harness(settings={},initialGrid=null){
  const nodes=new Map();
  const node=id=>{
    if(!nodes.has(id))nodes.set(id,mockElement(id));
    return nodes.get(id);
  };
  const base=[
    {clock:[77,70,180,110],weather:[77,255,350,140],
      calendar:[950,80,240,270],footer:[60,665,420,30]},
    {clock:[800,70,180,110],weather:[800,255,350,140],
      calendar:[80,80,240,270],footer:[600,665,420,30]},
  ];
  const screenIds={clockCard:'clock',weatherCard:'weather',
    calendarCard:'calendar',clipFooter:'footer'};
  const active={preset:0,layout:{}};
  const screenNodes=new Map(Object.entries(screenIds).map(([id,name])=>[
    id,{
      hidden:false,className:'',
      getBoundingClientRect(){
        const [x,y,width,height]=base[active.preset][name];
        const geometry=active.layout[name];
        return geometry?{left:geometry.x*12.8,top:geometry.y*7.2,
          width:width*geometry.scale,height:height*geometry.scale}
          :{left:x,top:y,width,height};
      },
    },
  ]));
  const frame=node('layoutFrame');
  frame.contentDocument={getElementById:id=>screenNodes.get(id)};
  frame.contentWindow={
    previewOsdPreset(index){active.preset=index;active.layout={};},
    applyOsdLayout(layout){active.layout=layout;},
  };
  const document={getElementById:node,createElement:mockElement};
  const preference=new Map();
  if(initialGrid!==null)preference.set('pi-aerial-layout-grid',initialGrid);
  const context={document,PiI18n:{translate:text=>text},
    localStorage:{getItem:key=>preference.get(key)||null,setItem:(key,value)=>preference.set(key,value)},
    window:{addEventListener(){}},setTimeout(){},ResizeObserver:undefined};
  const editor=vm.runInNewContext(source+'\nPiLayoutEditor;',context);
  const config={osd_layout_mode:'alternate',osd_layout:{},osd_layout_alt:{},...settings};
  let saved=null;
  editor.open({status:{config},
    api:async(path,options)=>{
      assert.equal(path,'/api/admin/config');
      saved=structuredClone(options.body.changes);
      Object.assign(config,saved);
      return {config};
    },
    load:async()=>{},toast(){}});
  frame.dispatch('load');
  const selectPosition=index=>{
    node('layoutPosition').value=String(index);
    node('layoutPosition').dispatch('change',{target:node('layoutPosition')});
  };
  const setScale=scale=>{
    node('layoutScale').value=String(scale);
    node('layoutScale').dispatch('input',{target:node('layoutScale')});
  };
  const setMode=mode=>{
    node('layoutMode').value=mode;
    node('layoutMode').dispatch('change',{target:node('layoutMode')});
  };
  const save=()=>node('layoutSave').dispatch('click');
  const reset=()=>node('layoutReset').dispatch('click');
  const grid=enabled=>{
    node('layoutSnapGrid').checked=enabled;
    node('layoutSnapGrid').dispatch('change',{target:node('layoutSnapGrid')});
  };
  const drag=(name,dx,dy,pointerId=10)=>{
    const item=node('layoutOverlay').children[Object.keys(screenIds).indexOf(name)];
    const target={classList:{contains:()=>false}};
    item.dispatch('pointerdown',{button:0,pointerId,clientX:0,clientY:0,target,
      preventDefault(){}});
    item.dispatch('pointermove',{pointerId,clientX:dx,clientY:dy});
    item.dispatch('pointerup',{pointerId});
  };
  return {node,config,selectPosition,setScale,setMode,save,reset,grid,drag,preference,
    get saved(){return saved;}};
}

test('each OSD position is edited independently, never overwriting the other',async()=>{
  const ui=harness();
  ui.selectPosition(1);
  ui.setScale(130);
  await ui.save();
  assert.deepEqual(ui.saved.osd_layout,{});
  assert.equal(ui.saved.osd_layout_alt.clock.scale,1.3);
  const secondClock=structuredClone(ui.saved.osd_layout_alt.clock);

  ui.selectPosition(0);
  ui.setScale(110);
  await ui.save();
  assert.equal(ui.saved.osd_layout.clock.scale,1.1);
  assert.deepEqual(ui.saved.osd_layout_alt.clock,secondClock);
});

test('single mode hides the second selector without deleting its saved layout',async()=>{
  const ui=harness();
  ui.selectPosition(1);
  ui.setScale(125);
  await ui.save();
  const second=structuredClone(ui.saved.osd_layout_alt);

  ui.setMode('single');
  assert.equal(ui.node('layoutPositionField').hidden,true);
  assert.equal(ui.node('layoutPosition').value,'0');
  await ui.save();
  assert.equal(ui.saved.osd_layout_mode,'single');
  assert.deepEqual(ui.saved.osd_layout_alt,second);

  ui.setMode('alternate');
  assert.equal(ui.node('layoutPositionField').hidden,false);
  await ui.save();
  assert.equal(ui.saved.osd_layout_mode,'alternate');
  assert.deepEqual(ui.saved.osd_layout_alt,second);
});

test('reset clears both layouts and restores default alternation',async()=>{
  const ui=harness();
  ui.setScale(140);
  ui.selectPosition(1);
  ui.setScale(90);
  ui.setMode('single');
  ui.reset();
  await ui.save();
  assert.deepEqual(ui.saved,{
    osd_layout_mode:'alternate',osd_layout:{},osd_layout_alt:{},
  });
});

test('grid is visible and snapping enabled by default for dragged objects',async()=>{
  const ui=harness();
  assert.equal(ui.node('layoutSnapGrid').checked,true);
  assert.equal(ui.node('layoutStage').classList.contains('show-grid'),true);
  ui.drag('clock',64,36);
  await ui.save();
  assert.equal(ui.saved.osd_layout.clock.x,15.63); // 5 of 32 columns
  assert.equal(ui.saved.osd_layout.clock.y,22.22); // 4 of 18 rows
  assert.equal(ui.saved.osd_layout_alt.clock,undefined);
});

test('turning grid off allows free positioning without changing existing coordinates',async()=>{
  const ui=harness();
  ui.drag('clock',64,36);
  await ui.save();
  assert.equal(ui.saved.osd_layout.clock.x,15.63);
  ui.grid(false);
  assert.equal(ui.node('layoutSnapGrid').checked,false);
  assert.equal(ui.node('layoutStage').classList.contains('show-grid'),false);
  assert.equal(ui.preference.get('pi-aerial-layout-grid'),'off');
  await ui.save();
  assert.equal(ui.saved.osd_layout.clock.x,15.63); // Toggle alone does not move anything.
  ui.drag('clock',19,0);
  await ui.save();
  assert.equal(ui.saved.osd_layout.clock.x,18.6); // Fractional, not snapped.
  ui.grid(true);
  assert.equal(ui.node('layoutStage').classList.contains('show-grid'),true);
  assert.equal(ui.preference.get('pi-aerial-layout-grid'),'on');
  await ui.save();
  assert.equal(ui.saved.osd_layout.clock.x,18.6); // Re-enabling does not alter saved geometry.
});

test('each alternate OSD position snaps independently while editing',async()=>{
  const ui=harness();
  ui.selectPosition(1);
  ui.drag('clock',64,36);
  await ui.save();
  assert.deepEqual(ui.saved.osd_layout,{});
  assert.equal(ui.saved.osd_layout_alt.clock.x,78.13); // Preset 2 starts on the right.
  ui.selectPosition(0);
  ui.drag('clock',64,36);
  await ui.save();
  assert.equal(ui.saved.osd_layout.clock.x,15.63);
  assert.equal(ui.saved.osd_layout_alt.clock.x,78.13);
});

test('grid toggle preference persists between editor openings',()=>{
  const ui=harness({},'off');
  assert.equal(ui.node('layoutSnapGrid').checked,false);
  assert.equal(ui.node('layoutStage').classList.contains('show-grid'),false);
});

test('alignment grid exists in admin styling only, not on the TV display',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../web/admin.css'),'utf8');
  const display=fs.readFileSync(path.join(__dirname,'../web/display.css'),'utf8');
  assert.match(css,/\.layout-stage\.show-grid:after/);
  assert.doesNotMatch(display,/show-grid/);
});
