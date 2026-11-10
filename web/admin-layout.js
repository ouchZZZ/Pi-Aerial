'use strict';
/* Visual OSD editor. The iframe runs the actual display markup without video decoding. */
const PiLayoutEditor=(()=>{
  const FRAME_WIDTH=1280,FRAME_HEIGHT=720;
  const GRID_COLUMNS=32,GRID_ROWS=18; // 40px square cells in the reference preview.
  const ids={clock:'clockCard',weather:'weatherCard',calendar:'calendarCard',footer:'clipFooter'};
  const captions={clock:'Часы',weather:'Погода',calendar:'Календарь',footer:'Описание'};
  const fallbacks={clock:[6,10],weather:[6,36],calendar:[74,10],footer:[5,92]};
  const items={};
  let started=false,ready=false,drafts=[{},{}],mode='alternate',position=0,selected='clock',drag=null,services=null;
  let gridEnabled=true;
  const active=()=>drafts[position];
  const node=id=>document.getElementById(id);
  const round=n=>Math.round(n*100)/100;
  const clamp=(n,min,max)=>Math.min(max,Math.max(min,n));
  const copy=value=>JSON.parse(JSON.stringify(value));
  const translate=value=>PiI18n.translate(value);
  const stage=()=>node('layoutStage');
  const frame=()=>node('layoutFrame');
  const screen=()=>frame().contentDocument;

  function geometry(){
    const result={};
    for(const [name,id] of Object.entries(ids)){
      const element=screen().getElementById(id);
      const box=element.getBoundingClientRect();
      const [fallbackX,fallbackY]=fallbacks[name];
      result[name]={
        x:round(box.width?100*box.left/FRAME_WIDTH:fallbackX),
        y:round(box.height?100*box.top/FRAME_HEIGHT:fallbackY),
        scale:1
      };
    }
    return result;
  }
  function ensureGeometry(){
    if(!Object.keys(active()).length)drafts[position]=geometry();
  }
  function setMessage(message){node('layoutMessage').textContent=translate(message);}
  // The grid is an editor preference, not a property of the video display.
  function restoreGridPreference(){
    try{gridEnabled=localStorage.getItem('pi-aerial-layout-grid')!=='off';}
    catch{gridEnabled=true;}
    updateGrid();
  }
  function updateGrid(){
    stage().classList.toggle('show-grid',gridEnabled);
    node('layoutSnapGrid').checked=gridEnabled;
  }
  function setGrid(enabled){
    gridEnabled=Boolean(enabled);
    updateGrid();
    try{localStorage.setItem('pi-aerial-layout-grid',gridEnabled?'on':'off');}
    catch{/* Private browsing or storage disabled: grid still works for this session. */}
  }
  function snapCoordinate(value,divisions,maximum){
    const step=100/divisions;
    const nearest=Math.round(clamp(value,0,maximum)/step);
    const last=Math.floor((maximum+1e-8)/step);
    return round(Math.min(nearest,last)*step);
  }
  function setSelected(name){
    if(!ids[name])return;
    selected=name;
    node('layoutBlock').value=name;
    for(const [key,item] of Object.entries(items))item.classList.toggle('selected',key===name);
    const scale=active()[name]?.scale||1;
    node('layoutScale').value=Math.round(scale*100);
    node('layoutScaleValue').textContent=Math.round(scale*100)+'%';
  }
  function apply(){
    if(!ready)return;
    // The preview uses the real legacy position as a starting point for each
    // layout; the active custom geometry, if any, is applied on top.
    frame().contentWindow.previewOsdPreset(position);
    frame().contentWindow.applyOsdLayout(active());
    render();
  }
  function updatePositionControls(){
    node('layoutMode').value=mode;
    if(mode==='single')position=0;
    node('layoutPositionField').hidden=mode==='single';
    node('layoutPosition').value=String(position);
    apply();
  }
  function render(){
    if(!ready)return;
    for(const [name,item] of Object.entries(items)){
      const element=screen().getElementById(ids[name]);
      const rect=element.getBoundingClientRect();
      item.style.display=element.hidden?'none':'block';
      item.style.left=(rect.left/FRAME_WIDTH*100)+'%';
      item.style.top=(rect.top/FRAME_HEIGHT*100)+'%';
      item.style.width=(rect.width/FRAME_WIDTH*100)+'%';
      item.style.height=(rect.height/FRAME_HEIGHT*100)+'%';
    }
    setSelected(selected);
  }
  function fitPosition(name){
    const current=active()[name],bounds=screen().getElementById(ids[name]).getBoundingClientRect();
    const maxX=Math.max(0,100-bounds.width/FRAME_WIDTH*100);
    const maxY=Math.max(0,100-bounds.height/FRAME_HEIGHT*100);
    current.x=gridEnabled?snapCoordinate(current.x,GRID_COLUMNS,maxX):round(clamp(current.x,0,maxX));
    current.y=gridEnabled?snapCoordinate(current.y,GRID_ROWS,maxY):round(clamp(current.y,0,maxY));
  }
  function changed(){setMessage('Есть несохранённые изменения');}
  function startDrag(event,name,item){
    if(!ready||event.button!==0)return;
    event.preventDefault();
    ensureGeometry();
    setSelected(name);
    const size=item.getBoundingClientRect();
    drag={name,pointer:event.pointerId,x:event.clientX,y:event.clientY,
      w:Math.max(1,size.width),h:Math.max(1,size.height),
      start:copy(active()[name]),
      resize:event.target.classList.contains('layout-item-resize')};
    item.setPointerCapture(event.pointerId);
    item.classList.add('dragging');
  }
  function moveDrag(event){
    if(!drag||drag.pointer!==event.pointerId)return;
    const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
    const current=active()[drag.name],start=drag.start;
    if(drag.resize){
      const multiplier=Math.max(.1,Math.max(1+dx/drag.w,1+dy/drag.h));
      current.scale=round(clamp(start.scale*multiplier,.4,2.5));
    }else{
      const bounds=stage().getBoundingClientRect();
      current.x=round(start.x+dx/bounds.width*100);
      current.y=round(start.y+dy/bounds.height*100);
    }
    apply();
    fitPosition(drag.name);
    apply();
    changed();
  }
  function finishDrag(event,item){
    if(!drag||drag.pointer!==event.pointerId)return;
    drag=null;
    item.classList.remove('dragging');
    if(item.hasPointerCapture?.(event.pointerId))item.releasePointerCapture(event.pointerId);
  }
  function addHandles(){
    const overlay=node('layoutOverlay');
    for(const name of Object.keys(ids)){
      const item=document.createElement('div');
      item.className='layout-item';
      item.setAttribute('role','button');
      item.setAttribute('tabindex','0');
      item.setAttribute('aria-label',translate('Переместить или изменить размер: ')+translate(captions[name]));
      const caption=document.createElement('span');
      caption.className='layout-item-label';
      caption.textContent=translate(captions[name]);
      const grip=document.createElement('span');
      grip.className='layout-item-resize';
      grip.setAttribute('aria-hidden','true');
      item.append(caption,grip);
      item.addEventListener('pointerdown',event=>startDrag(event,name,item));
      item.addEventListener('pointermove',moveDrag);
      item.addEventListener('pointerup',event=>finishDrag(event,item));
      item.addEventListener('pointercancel',event=>finishDrag(event,item));
      item.addEventListener('keydown',event=>{
        if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
        event.preventDefault();ensureGeometry();setSelected(name);
        // With snapping active, arrow keys advance exactly one grid cell.
        // Shift advances three cells; with snapping off, use fine 0.25% steps.
        const multiple=event.shiftKey?3:1;
        const stepX=gridEnabled?100/GRID_COLUMNS*multiple:(event.shiftKey?1:0.25);
        const stepY=gridEnabled?100/GRID_ROWS*multiple:(event.shiftKey?1:0.25);
        if(event.key==='ArrowLeft')active()[name].x-=stepX;
        if(event.key==='ArrowRight')active()[name].x+=stepX;
        if(event.key==='ArrowUp')active()[name].y-=stepY;
        if(event.key==='ArrowDown')active()[name].y+=stepY;
        apply();fitPosition(name);apply();changed();
      });
      items[name]=item;
      overlay.append(item);
    }
  }
  function resizeFrame(){
    const width=stage().getBoundingClientRect().width;
    frame().style.transform='scale('+(width/FRAME_WIDTH)+')';
    render();
  }
  function onLoad(){
    if(!frame().contentWindow.applyOsdLayout||!frame().contentWindow.previewOsdPreset)return;
    ready=true;
    drafts=[
      copy(services.status.config.osd_layout||{}),
      copy(services.status.config.osd_layout_alt||{}),
    ];
    mode=services.status.config.osd_layout_mode==='single'?'single':'alternate';
    position=0;
    updatePositionControls();
    resizeFrame();
    // API weather text arrives asynchronously after the iframe's load event.
    setTimeout(render,800);
    setMessage('Изменения ещё не сохранены');
  }
  async function save(){
    if(!ready)return;
    const button=node('layoutSave');button.disabled=true;
    try{
      const changes={
        osd_layout_mode:mode,
        osd_layout:copy(drafts[0]),
        osd_layout_alt:copy(drafts[1]),
      };
      const result=await services.api('/api/admin/config',{method:'PUT',body:{changes}});
      services.status.config.osd_layout=copy(result.config.osd_layout);
      services.status.config.osd_layout_alt=copy(result.config.osd_layout_alt);
      services.status.config.osd_layout_mode=result.config.osd_layout_mode;
      await services.load();
      setMessage('Расположение сохранено');
      services.toast('Расположение сохранено');
    }catch(error){setMessage(error.message);services.toast(error.message);}
    finally{button.disabled=false;}
  }
  function reset(){
    if(!ready)return;
    drafts=[{},{}];mode='alternate';position=0;
    updatePositionControls();setSelected(selected);changed();
  }
  function init(){
    addHandles();
    restoreGridPreference();
    node('layoutSnapGrid').addEventListener('change',event=>setGrid(event.target.checked));
    node('layoutMode').addEventListener('change',e=>{
      if(!ready)return;
      mode=e.target.value==='single'?'single':'alternate';
      updatePositionControls();changed();
    });
    node('layoutPosition').addEventListener('change',e=>{
      if(!ready||mode==='single')return;
      position=e.target.value==='1'?1:0;
      apply();
    });
    node('layoutBlock').addEventListener('change',e=>setSelected(e.target.value));
    node('layoutScale').addEventListener('input',event=>{
      if(!ready)return;
      ensureGeometry();
      active()[selected].scale=Number(event.target.value)/100;
      apply();fitPosition(selected);apply();changed();
    });
    node('layoutSave').addEventListener('click',save);
    node('layoutReset').addEventListener('click',reset);
    frame().addEventListener('load',onLoad);
    if(typeof ResizeObserver!=='undefined')new ResizeObserver(resizeFrame).observe(stage());
    else window.addEventListener('resize',resizeFrame);
    started=true;
  }
  return {open(context){
    services=context;
    if(!started)init();
    if(!frame().getAttribute('src'))frame().src='/?layout_preview=1';
    resizeFrame();
  }};
})();
