'use strict';
const $ = id => document.getElementById(id);
let state=null, videos=[], videoPos=-1, changeTimer=null, lastReload=null, selectedFile='';
const rejectedUntil=new Map();
const video=$('video');
const wxText={0:'Ясно',1:'Преимущественно ясно',2:'Переменная облачность',3:'Пасмурно',45:'Туман',48:'Туман',51:'Морось',53:'Морось',55:'Морось',61:'Небольшой дождь',63:'Дождь',65:'Сильный дождь',71:'Небольшой снег',73:'Снег',75:'Сильный снег',80:'Ливни',81:'Ливни',82:'Сильные ливни',95:'Гроза',96:'Гроза',99:'Гроза'};
function iconFor(code,isDay){if([0,1].includes(code))return isDay?'☀':'☾';if(code<=3)return '☁';if(code<=48)return '≋';if(code>=71&&code<=77)return '❄';if(code>=95)return 'ϟ';return '☂';}
function zoneDateParts(){let c=state?.settings||{};const zone=c.timezone||'UTC';const now=new Date();const h=new Intl.DateTimeFormat('en-GB',{timeZone:zone,hour:'2-digit',hourCycle:'h23'}).format(now);return {now,hour:Number(h)};}
function drawTime(){if(!state)return;const c=state.settings;const {now,hour}=zoneDateParts();let locale='ru-RU';$('clock').textContent=new Intl.DateTimeFormat(locale,{timeZone:c.timezone,hour:'2-digit',minute:'2-digit',hourCycle:c.time_format==='12'?'h12':'h23'}).format(now);const date=new Intl.DateTimeFormat(locale,{timeZone:c.timezone,weekday:'long',day:'numeric',month:'long'}).format(now);$('date').textContent=date.charAt(0).toUpperCase()+date.slice(1);$('topDate').textContent=c.city;
const night=c.night_mode&&(c.night_start<=c.night_end?(hour>=c.night_start&&hour<c.night_end):(hour>=c.night_start||hour<c.night_end));$('nightShade').style.opacity=night?String(1-c.night_opacity):'0';
// Move fixed UI elements just a little to reduce persistent static pixels on OLED.
const offset=c.clock_shift?Math.floor(now.getTime()/600000)%4:0;const dx=[0,14,-9,6][offset],dy=[0,-8,10,3][offset];$('clockCard').style.marginLeft=dx+'px';$('clockCard').style.marginTop=dy+'px';}
function showWeather(){if(!state)return;const w=state.weather,c=state.settings;$('clock').hidden=!c.show_clock;$('date').hidden=!c.show_date;$('weather').hidden=!c.show_weather;if(!c.show_weather)return;
if(!w.available){$('wxTemp').textContent='Погода недоступна';$('wxDetail').textContent='Ожидаем соединения';$('wxIcon').textContent='◌';return;}
$('wxIcon').textContent=iconFor(Number(w.code),w.is_day);$('wxTemp').textContent=Math.round(w.temperature)+'°C · '+(wxText[w.code]||'Погода');$('wxDetail').textContent=`Ощущается ${Math.round(w.apparent)}° · ветер ${Math.round(w.wind)} км/ч`+(w.stale?` · данные ${w.age_minutes} мин. назад`:'');}
function listChanged(list){return list.map(x=>x.id||x.url).join('|')!==videos.map(x=>x.id||x.url).join('|');}
function changeVideo(){
  clearTimeout(changeTimer);
  const now=Date.now();
  const candidates=videos.map((v,i)=>({v,i})).filter(x=>(rejectedUntil.get(x.v.id||x.v.url)||0)<now);
  if(!candidates.length){
    video.pause();video.classList.remove('ready');video.removeAttribute('src');video.load();
    $('clipName').textContent=videos.length?'НЕ УДАЛОСЬ ПОЛУЧИТЬ ВИДЕО · ПОВТОР ЧЕРЕЗ 60 С':'НЕТ ВИДЕО · ВКЛЮЧИТЕ ИСТОЧНИКИ';
    $('videoIndex').textContent='';
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
  videoPos=selected.i;selectedFile=selected.v.id||selected.v.url;
  $('clipName').textContent=selected.v.credit||'PI AERIAL · ВИДЕОЗАСТАВКА';
  $('videoIndex').textContent=`${selected.i+1} / ${videos.length}`;
  video.classList.remove('ready');
  video.src=selected.v.url;video.load();
  video.play().then(()=>video.classList.add('ready')).catch(()=>{
    rejectedUntil.set(selectedFile,Date.now()+180000);
    $('clipName').textContent='НЕ УДАЛОСЬ ВОСПРОИЗВЕСТИ ВИДЕО';
    changeTimer=setTimeout(changeVideo,3000);
  });
  const minutes=Number(cfg.video_interval_minutes);
  if(minutes>0)changeTimer=setTimeout(changeVideo,minutes*60000);
}
function renderModules(){const layer=$('moduleLayer');layer.replaceChildren();for(const m of state.modules||[]){const frame=document.createElement('iframe');frame.className='widget '+(m.position||'top-right');frame.sandbox='allow-scripts';frame.title=m.name||m.id;frame.src='/modules/'+encodeURIComponent(m.id);frame.addEventListener('load',()=>sendModuleData(frame));layer.appendChild(frame);}}
function sendModuleData(frame){frame.contentWindow?.postMessage({type:'aerial:data',config:state?.settings,weather:state?.weather,time:Date.now()},'*');}
async function poll(){try{const r=await fetch('/api/public',{cache:'no-store'});if(!r.ok)throw Error(r.status);const next=await r.json();const first=!state;const oldIds=state?.modules.map(x=>x.id+':'+x.version).join('|');const newIds=next.modules.map(x=>x.id+':'+x.version).join('|');const shouldReload=lastReload!==null&&next.reload_counter!==lastReload;lastReload=next.reload_counter;if(shouldReload){location.reload();return;}const shouldChange=listChanged(next.videos);state=next;videos=next.videos;$('clockCard').className='clock-card '+state.settings.clock_position;drawTime();showWeather();if(first||shouldChange)changeVideo();if(first||oldIds!==newIds)renderModules();for(const frame of $('moduleLayer').querySelectorAll('iframe'))sendModuleData(frame);}catch(e){console.warn('Pi Aerial: status unavailable',e);}}
video.addEventListener('ended',changeVideo);video.addEventListener('error',()=>{video.classList.remove('ready');rejectedUntil.set(selectedFile,Date.now()+180000);clearTimeout(changeTimer);changeTimer=setTimeout(changeVideo,3500);});
setInterval(drawTime,1000);setInterval(poll,30000);poll();
