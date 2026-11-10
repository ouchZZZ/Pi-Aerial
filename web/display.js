'use strict';
const $ = id => document.getElementById(id);
let state=null, videos=[], videoPos=-1, changeTimer=null, lastReload=null, selectedFile='';
let playAttempt=0, failedAttempt=-1, selectedVideo=null;
let osdRight=false, hasPlayedVideo=false;
let playingOnce=false, pollingDisplayRequest=false;
const rejectedUntil=new Map();
const video=$('video');
const wxTextRu={0:'Ясно',1:'Преимущественно ясно',2:'Переменная облачность',3:'Пасмурно',45:'Туман',48:'Туман',51:'Морось',53:'Морось',55:'Морось',61:'Небольшой дождь',63:'Дождь',65:'Сильный дождь',71:'Небольшой снег',73:'Снег',75:'Сильный снег',80:'Ливни',81:'Ливни',82:'Сильные ливни',95:'Гроза',96:'Гроза',99:'Гроза'};

const wxTextEn={0:'Clear',1:'Mostly clear',2:'Partly cloudy',3:'Overcast',45:'Fog',48:'Fog',51:'Drizzle',53:'Drizzle',55:'Drizzle',61:'Light rain',63:'Rain',65:'Heavy rain',71:'Light snow',73:'Snow',75:'Heavy snow',80:'Showers',81:'Showers',82:'Heavy showers',95:'Thunderstorm',96:'Thunderstorm',99:'Thunderstorm'};
function displayLanguage(){return state?.settings?.language==='en'?'en':'ru';}
function conditionText(code){const english=displayLanguage()==='en';return (english?wxTextEn:wxTextRu)[code]||(english?'Weather':'Погода');}
function displayText(ru,en){return displayLanguage()==='en'?en:ru;}
function localeName(){return displayLanguage()==='en'?'en-GB':'ru-RU';}

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
let calendarDay;
const publicHolidays=new Set(['02-23','03-08','05-01','05-09','06-12','11-04']);
// Government resolutions No. 1466 (2026) and No. 1187 (2027).
const extraDaysOff=new Set(['2026-01-09','2026-12-31','2027-02-22','2027-11-05','2027-12-31']);
const workingWeekends=new Set(['2027-02-20']);
function isPublicHoliday(date){
  const key=date.toISOString().slice(5,10);
  return (key>='01-01'&&key<='01-08')||publicHolidays.has(key);
}
function calendarDayClass(date){
  if(isPublicHoliday(date))return 'holiday';
  const key=date.toISOString().slice(0,10);
  if(workingWeekends.has(key))return '';
  if(extraDaysOff.has(key)||[0,6].includes(date.getUTCDay()))return 'weekend';
  // A holiday falling on a weekend outside the January holidays moves to the next workday.
  const previous=new Date(date);
  previous.setUTCDate(previous.getUTCDate()-1);
  while([0,6].includes(previous.getUTCDay())||isPublicHoliday(previous)){
    if(previous.getUTCMonth()!==0&&[0,6].includes(previous.getUTCDay())&&isPublicHoliday(previous))return 'weekend';
    previous.setUTCDate(previous.getUTCDate()-1);
  }
  return '';
}
function drawCalendar(now, timeZone){
  const dayKey=new Intl.DateTimeFormat('sv-SE',{timeZone,year:'numeric',month:'2-digit',day:'2-digit'}).format(now);
  if(dayKey+'|'+displayLanguage()===calendarDay)return;
  calendarDay=dayKey+'|'+displayLanguage();
  const [year,month,day]=dayKey.split('-').map(Number);
  setText('weekday',new Intl.DateTimeFormat(localeName(),{timeZone,weekday:'long'}).format(now));
  setText('date',new Intl.DateTimeFormat(localeName(),{timeZone,day:'numeric',month:'long',year:'numeric'}).format(now));
  const firstDay=(new Date(Date.UTC(year,month-1,1)).getUTCDay()+6)%7;
  const days=new Date(Date.UTC(year,month,0)).getUTCDate();
  const headings=displayLanguage()==='en'?['Mon','Tue','Wed','Thu','Fri','Sat','Sun']:['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
  $('monthGrid').innerHTML=headings.map(label=>'<span class="day-label">'+label+'</span>').join('')+
    Array.from({length:Math.ceil((firstDay+days)/7)*7},(_,index)=>{
      const number=index-firstDay+1;
      const date=new Date(Date.UTC(year,month-1,number));
      const kind=number<1?'previous-month':number>days?'next-month':calendarDayClass(date);
      const current=number===day?' aria-current="date"':'';
      return '<span class="'+kind+'"'+current+'>'+date.getUTCDate()+'</span>';
    }).join('');
}
function drawTime(){
  if(!state)return;
  const c=state.settings;
  const {now,hour}=zoneDateParts();
  const locale=localeName();
  const clock=new Intl.DateTimeFormat(locale,{timeZone:c.timezone,hour:'2-digit',minute:'2-digit',hourCycle:c.time_format==='12'?'h12':'h23'}).format(now);
  setText('clock',clock);
  if(c.show_date)drawCalendar(now,c.timezone);
  setText('city',displayLanguage()==='en'&&c.city==='Москва'?'Moscow':(c.city||''));
  const night=c.night_mode&&(c.night_start<=c.night_end?(hour>=c.night_start&&hour<c.night_end):(hour>=c.night_start||hour<c.night_end));
  setStyle('nightShade','opacity',night?String(1-c.night_opacity):'0');
}
const windSpeedFor=()=>new Intl.NumberFormat(localeName(),{maximumFractionDigits:1});
let forecastMarkup='';
function showForecast(weather, settings){
  const today=new Intl.DateTimeFormat('sv-SE',{timeZone:settings.timezone}).format(new Date());
  const days=settings.show_weather&&weather.available
    ? (weather.forecast||[]).filter(day=>day.date>today&&Number.isFinite(day.min)&&Number.isFinite(day.max)).slice(0,7)
    : [];
  const markup=days.map(day=>{
    const date=new Date(day.date+'T00:00:00Z');
    const label=new Intl.DateTimeFormat(localeName(),{timeZone:'UTC',weekday:'short'}).format(date);
    const icon=iconFor(day.code,true);
    const condition=conditionText(day.code);
    return '<div class="forecast-day"><span>'+label+'</span><span class="forecast-icon" role="img" aria-label="'+condition+'">'+icon+'</span><span>'+Math.round(day.max)+'°</span><span class="forecast-low">'+Math.round(day.min)+'°</span></div>';
  }).join('');
  if(markup!==forecastMarkup){
    $('forecast').innerHTML=markup;
    forecastMarkup=markup;
  }
  setHidden('forecast',!markup);
}
function showWeather(){
  if(!state)return;
  const w=state.weather,c=state.settings;
  setText('weatherCredit',displayText('Погода: Open-Meteo','Weather: Open-Meteo'));
  $('forecast').setAttribute?.('aria-label',displayText('Прогноз на неделю','Weekly forecast'));
  setHidden('clock',!c.show_clock);
  setHidden('calendarCard',!c.show_date);
  setHidden('clockCard',!c.show_clock&&!c.show_weather);
  setHidden('city',!c.show_weather);
  setHidden('weather',!c.show_weather);
  showForecast(w,c);
  if(!c.show_weather)return;
  if(!w.available){
    setText('wxTemp',displayText('Погода недоступна','Weather unavailable'));
    setText('wxDetail',displayText('Ожидаем соединения','Waiting for connection'));
    setText('wxIcon','◌');
    return;
  }
  setText('wxIcon',iconFor(Number(w.code),w.is_day));
  setText('wxTemp',Math.round(w.temperature)+'°C · '+conditionText(w.code));
  setText('wxDetail',displayLanguage()==='en'
    ? 'Feels like '+Math.round(w.apparent)+'° · wind '+windSpeedFor().format(w.wind/3.6)+' m/s'+(w.stale?' · updated '+w.age_minutes+' min ago':'')
    : 'Ощущается '+Math.round(w.apparent)+'° · ветер '+windSpeedFor().format(w.wind/3.6)+' м/с'+(w.stale?' · данные '+w.age_minutes+' мин. назад':''));
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
  console.error('Pi Aerial playback failed',{
    id:selectedFile,provider:selectedVideo?.provider||'local',name:selectedVideo?.name,
    error:name,message:err?.message||mediaError?.message||'',mediaError:code,
    networkState:video.networkState,readyState:video.readyState,
    h264Support:video.canPlayType('video/mp4; codecs="avc1.42E01E"')
  });
  if(playingOnce){
    changeVideo();
    return;
  }
  rejectedUntil.set(selectedFile,Date.now()+180000);
  video.classList.remove('ready');
  setText('clipName',displayText('ОШИБКА ВИДЕО: ','VIDEO ERROR: ')+name+displayText(' · код ',' · code ')+code);
  clearTimeout(changeTimer);
  changeTimer=setTimeout(changeVideo,4000);
}
function changeVideo(){
  clearTimeout(changeTimer);
  if(playingOnce){
    playingOnce=false;
    document.body.classList.remove('playing-once');
  }
  const now=Date.now();
  const candidates=videos.map((v,i)=>({v,i})).filter(x=>(rejectedUntil.get(x.v.id||x.v.url)||0)<now);
  if(!candidates.length){
    ++playAttempt;
    selectedFile='';
    selectedVideo=null;
    video.classList.remove('ready');
    // Once idle, stop touching the media pipeline until a new clip arrives.
    if(video.hasAttribute('src')){
      video.pause();
      video.removeAttribute('src');
      video.load();
    }
    setText('clipName',videos.length
      ?displayText('НЕ УДАЛОСЬ ПОЛУЧИТЬ ВИДЕО · ПОВТОР ЧЕРЕЗ 60 С','VIDEO UNAVAILABLE · RETRY IN 60 S')
      :(state?.settings?.online_sources?.apple
        ?displayText('ПОДГОТОВКА APPLE AERIAL · ЗАГРУЗКА В КЕШ','PREPARING APPLE AERIAL · CACHING')
        :displayText('НЕТ ВИДЕО · ВКЛЮЧИТЕ ИСТОЧНИКИ','NO VIDEOS · ENABLE SOURCES')));
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
  playVideo(selected.v);
  const minutes=Number(cfg.video_interval_minutes);
  if(minutes>0)changeTimer=setTimeout(changeVideo,minutes*60000);
}
function playVideo(clip){
  const attempt=++playAttempt;
  selectedFile=clip.id||clip.url;
  selectedVideo=clip;
  setText('clipName',clip.credit||displayText('ВИДЕОЗАСТАВКА','VIDEO SCREENSAVER'));

  renderClipDescription(0);
  video.classList.remove('ready');
  video.src=clip.url;
  video.load();
  if(playingOnce){
    changeTimer=setTimeout(()=>reportPlaybackFailure(attempt,new Error('Время запуска видео истекло')),15000);
  }
  video.play().then(()=>{
    if(attempt!==playAttempt || failedAttempt===attempt)return;
    if(playingOnce){
      clearTimeout(changeTimer);
    }else{
      if(hasPlayedVideo)osdRight=!osdRight;
      hasPlayedVideo=true;
      renderOsdPosition();
    }
    video.classList.add('ready');
  }).catch(err=>reportPlaybackFailure(attempt,err));
}
async function pollDisplayRequest(){
  if(!state||pollingDisplayRequest)return;
  pollingDisplayRequest=true;
  try{
    const r=await fetch('/api/display/play-once',{cache:'no-store'});
    if(!r.ok)throw Error(r.status);
    const request=await r.json();
    if(request?.stop){
      if(playingOnce)changeVideo();
      return;
    }
    if(!request||request.id===sessionStorage.getItem('lastDisplayRequest'))return;
    sessionStorage.setItem('lastDisplayRequest',request.id);
    clearTimeout(changeTimer);
    playingOnce=true;
    document.body.classList.add('playing-once');
    playVideo(request);
  }catch(e){console.warn('Pi Aerial: display request unavailable',e);}
  finally{pollingDisplayRequest=false;}
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
function renderOsdPosition(){
  const sideClass=osdRight?' osd-right':'';
  const positionClass='clock-card'+sideClass;
  const calendarClass='calendar-card'+(osdRight?'':' osd-right');
  if($('calendarCard').className!==calendarClass)$('calendarCard').className=calendarClass;
  if($('clockCard').className!==positionClass)$('clockCard').className=positionClass;
  const footerClass='footer'+sideClass;
  if($('clipFooter').className!==footerClass)$('clipFooter').className=footerClass;
}
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
    if(document.documentElement)document.documentElement.lang=displayLanguage();
    videos=next.videos;
    renderOsdPosition();
    drawTime();
    showWeather();
    if(!playingOnce&&(first||shouldChange))changeVideo();
    if(first||oldIds!==newIds)renderModules();
    // Preserve the 30-second widget heartbeat for third-party modules.
    for(const frame of $('moduleLayer').querySelectorAll('iframe'))sendModuleData(frame);
  }catch(e){console.warn('Pi Aerial: status unavailable',e);}
}
function renderClipDescription(second){
  let description=selectedVideo?.description||'';
  for(const caption of selectedVideo?.captions||[]){
    if(caption.second>second)break;
    description=caption.text;
  }
  description=description.trim().slice(0,220);
  setText('clipDescription',description);
  setHidden('clipDescription',!description);
}
video.addEventListener('timeupdate',()=>renderClipDescription(video.currentTime));
video.addEventListener('ended',changeVideo);
video.addEventListener('error',()=>reportPlaybackFailure(playAttempt,video.error));
setInterval(drawTime,10000);
setInterval(poll,30000);
setInterval(pollDisplayRequest,3000);
poll();
