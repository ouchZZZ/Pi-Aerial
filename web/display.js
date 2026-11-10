'use strict';
const $ = id => document.getElementById(id);
let state=null, videos=[], videoPos=-1, changeTimer=null, lastReload=null, selectedFile='';
let playAttempt=0, failedAttempt=-1, selectedVideo=null;
const rejectedUntil=new Map();
const video=$('video');
const wxText={0:'Ясно',1:'Преимущественно ясно',2:'Переменная облачность',3:'Пасмурно',45:'Туман',48:'Туман',51:'Морось',53:'Морось',55:'Морось',61:'Небольшой дождь',63:'Дождь',65:'Сильный дождь',71:'Небольшой снег',73:'Снег',75:'Сильный снег',80:'Ливни',81:'Ливни',82:'Сильные ливни',95:'Гроза',96:'Гроза',99:'Гроза'};

function setText(id, value){
  const element=$(id);
  if(element.textContent!==value)element.textContent=value;
}
function setHidden(id, hidden){
  const element=$(id);
  if(element.hidden!==hidden)element.hidden=hidden;
}
function setStyle(id, property, value){
  const element=$(id);
  if(element.style[property]!==value)element.style[property]=value;
}
function iconFor(code,isDay){if([0,1].includes(code))return isDay?'☀':'☾';if(code<=3)return '☁';if(code<=48)return '≋';if(code>=71&&code<=77)return '❄';if(code>=95)return 'ϟ';return '☂';}
function zoneDateParts(){
  const c=state?.settings||{};
  const zone=c.timezone||'UTC';
  const now=new Date();
  const h=new Intl.DateTimeFormat('en-GB',{timeZone:zone,hour:'2-digit',hourCycle:'h23'}).format(now);
  return {now,hour:Number(h)};
}
function drawTime(){
  if(!state)return;
  const c=state.settings;
  const {now,hour}=zoneDateParts();
  const locale='ru-RU';
  const clock=new Intl.DateTimeFormat(locale,{timeZone:c.timezone,hour:'2-digit',minute:'2-digit',hourCycle:c.time_format==='12'?'h12':'h23'}).format(now);
  const date=new Intl.DateTimeFormat(locale,{timeZone:c.timezone,weekday:'long',day:'numeric',month:'long'}).format(now);
  setText('clock',clock);
  setText('date',date.charAt(0).toUpperCase()+date.slice(1));
  setText('topDate',c.city);
  const night=c.night_mode&&(c.night_start<=c.night_end?(hour>=c.night_start&&hour<c.night_end):(hour>=c.night_start||hour<c.night_end));
  setStyle('nightShade','opacity',night?String(1-c.night_opacity):'0');
  // Move fixed UI elements just a little to reduce persistent static pixels on OLED.
  const offset=c.clock_shift?Math.floor(now.getTime()/600000)%4:0;
  const dx=[0,14,-9,6][offset],dy=[0,-8,10,3][offset];
  setStyle('clockCard','marginLeft',dx+'px');
  setStyle('clockCard','marginTop',dy+'px');
}
function showWeather(){
  if(!state)return;
  const w=state.weather,c=state.settings;
  setHidden('clock',!c.show_clock);
  setHidden('date',!c.show_date);
  setHidden('weather',!c.show_weather);
  if(!c.show_weather)return;
  if(!w.available){
    setText('wxTemp','Погода недоступна');
    setText('wxDetail','Ожидаем соединения');
    setText('wxIcon','◌');
    return;
  }
  setText('wxIcon',iconFor(Number(w.code),w.is_day));
  setText('wxTemp',Math.round(w.temperature)+'°C · '+(wxText[w.code]||'Погода'));
  setText('wxDetail','Ощущается '+Math.round(w.apparent)+'° · ветер '+Math.round(w.wind)+' км/ч'+(w.stale?' · данные '+w.age_minutes+' мин. назад':''));
}
function listChanged(list){return list.map(x=>x.id||x.url).join('|')!==videos.map(x=>x.id||x.url).join('|');}

// One failed playback attempt must only reject the clip and schedule a retry once.
// HTMLMediaElement.error and a rejected play() promise often report the same failure.
function reportPlaybackFailure(attempt,err){
  if(attempt!==playAttempt || failedAttempt===attempt || !selectedFile)return;
  failedAttempt=attempt;
  const mediaError=video.error;
  const code=mediaError?.code||0;
  const name=err?.name||(code?'MediaError':'неизвестная');
  rejectedUntil.set(selectedFile,Date.now()+180000);
  console.error('Pi Aerial playback failed',{
    id:selectedFile,provider:selectedVideo?.provider||'local',name:selectedVideo?.name,
    error:name,message:err?.message||mediaError?.message||'',mediaError:code,
    networkState:video.networkState,readyState:video.readyState,
    h264Support:video.canPlayType('video/mp4; codecs="avc1.42E01E"')
  });
  video.classList.remove('ready');
  setText('clipName','ОШИБКА ВИДЕО: '+name+' · код '+code);
  clearTimeout(changeTimer);
  changeTimer=setTimeout(changeVideo,4000);
}
function changeVideo(){
  clearTimeout(changeTimer);
  const attempt=++playAttempt;
  const now=Date.now();
  const candidates=videos.map((v,i)=>({v,i})).filter(x=>(rejectedUntil.get(x.v.id||x.v.url)||0)<now);
  if(!candidates.length){
    selectedFile='';
    selectedVideo=null;
    video.classList.remove('ready');
    // Once idle, stop touching the media pipeline until a new clip arrives.
    if(video.hasAttribute('src')){
      video.pause();
      video.removeAttribute('src');
      video.load();
    }
    setText('clipName',videos.length?'НЕ УДАЛОСЬ ПОЛУЧИТЬ ВИДЕО · ПОВТОР ЧЕРЕЗ 60 С':(state?.settings?.online_sources?.apple?'ПОДГОТОВКА APPLE AERIAL · ЗАГРУЗКА В КЕШ':'НЕТ ВИДЕО · ВКЛЮЧИТЕ ИСТОЧНИКИ'));
    setText('videoIndex','');
    setText('clipDescription','');
    setHidden('clipDescription',true);
    changeTimer=setTimeout(()=>{rejectedUntil.clear();changeVideo();},60000);
    return;
  }
  const cfg=state.settings;
  let selected;
  if(cfg.video_mode==='ordered'){
    selected=candidates.find(x=>x.i>videoPos)||candidates[0];
  }else{
    const pool=candidates.length>1?candidates.filter(x=>x.i!==videoPos):candidates;
    selected=pool[Math.floor(Math.random()*pool.length)]||candidates[0];
  }
  videoPos=selected.i;
  selectedFile=selected.v.id||selected.v.url;
  selectedVideo=selected.v;
  setText('clipName',selected.v.credit||'PI AERIAL · ВИДЕОЗАСТАВКА');
  const description=typeof selected.v.description==='string'
    ?selected.v.description.trim().slice(0,220):'';
  setText('clipDescription',description);
  setHidden('clipDescription',!description);
  setText('videoIndex',(selected.i+1)+' / '+videos.length);
  video.classList.remove('ready');
  video.src=selected.v.url;
  video.load();
  video.play().then(()=>{
    if(attempt===playAttempt && failedAttempt!==attempt)video.classList.add('ready');
  }).catch(err=>reportPlaybackFailure(attempt,err));
  const minutes=Number(cfg.video_interval_minutes);
  if(minutes>0)changeTimer=setTimeout(changeVideo,minutes*60000);
}
function renderModules(){
  const layer=$('moduleLayer');
  layer.replaceChildren();
  for(const m of state.modules||[]){
    const frame=document.createElement('iframe');
    frame.className='widget '+(m.position||'top-right');
    frame.sandbox='allow-scripts';
    frame.title=m.name||m.id;
    frame.src='/modules/'+encodeURIComponent(m.id);
    frame.addEventListener('load',()=>sendModuleData(frame));
    layer.appendChild(frame);
  }
}
function sendModuleData(frame){frame.contentWindow?.postMessage({type:'aerial:data',config:state?.settings,weather:state?.weather,time:Date.now()},'*');}
async function poll(){
  try{
    const r=await fetch('/api/public',{cache:'no-store'});
    if(!r.ok)throw Error(r.status);
    const next=await r.json();
    const first=!state;
    const oldIds=state?.modules.map(x=>x.id+':'+x.version).join('|');
    const newIds=next.modules.map(x=>x.id+':'+x.version).join('|');
    const shouldReload=lastReload!==null&&next.reload_counter!==lastReload;
    lastReload=next.reload_counter;
    if(shouldReload){location.reload();return;}
    const shouldChange=listChanged(next.videos);
    state=next;
    videos=next.videos;
    const positionClass='clock-card '+state.settings.clock_position;
    if($('clockCard').className!==positionClass)$('clockCard').className=positionClass;
    drawTime();
    showWeather();
    if(first||shouldChange)changeVideo();
    if(first||oldIds!==newIds)renderModules();
    // Preserve the 30-second widget heartbeat for third-party modules.
    for(const frame of $('moduleLayer').querySelectorAll('iframe'))sendModuleData(frame);
  }catch(e){console.warn('Pi Aerial: status unavailable',e);}
}
video.addEventListener('ended',changeVideo);
video.addEventListener('error',()=>reportPlaybackFailure(playAttempt,video.error));
setInterval(drawTime,10000);
setInterval(poll,30000);
poll();
