'use strict';
const $ = id => document.getElementById(id);
let state=null, videos=[], videoPos=-1, changeTimer=null, lastReload=null, selectedFile='';
let playAttempt=0, failedAttempt=-1, selectedVideo=null;
let osdRight=false, hasPlayedVideo=false;
let playingOnce=false, pollingDisplayRequest=false;
let embeddedPlayer=null, embeddedApiPromise=null, embeddedGeneration=0, embeddedTimer=null, embeddedActive=false;
let vkApiPromise=null;
let switchTimer=null, frameRequest=null, startupTimer=null, videoPresented=false;
const FADE_OUT_MS=400;
const rejectedUntil=new Map();
const video=$('video');
const layoutPreview=Boolean(location.search?.includes('layout_preview=1'));
if(layoutPreview)document.body.classList.add('layout-preview');
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
  setStyle('nightShade','opacity',night&&!layoutPreview?String(1-c.night_opacity):'0');
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
  setHidden('clockCard',!c.show_clock);
  setHidden('weatherCard',!c.show_weather);
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


// A single decoder stays in use. Black covers the source/layout swap, while
// the OSD fades independently and returns only after the new video has a frame.
function cancelMediaTransition(){
  clearTimeout(switchTimer);
  clearTimeout(startupTimer);
  switchTimer=startupTimer=null;
  if(frameRequest!==null)video.cancelVideoFrameCallback?.(frameRequest);
  frameRequest=null;
}
function finishMediaTransition(attempt){
  if(attempt!==playAttempt||failedAttempt===attempt)return;
  clearTimeout(startupTimer);
  startupTimer=null;
  frameRequest=null;
  if(!playingOnce){
    if(hasPlayedVideo)osdRight=!osdRight;
    hasPlayedVideo=true;
    renderOsdPosition();
  }
  videoPresented=true;
  video.classList.add('ready');
  document.body.classList.remove('media-transition');
  const minutes=Number(state?.settings?.video_interval_minutes);
  if(!playingOnce&&minutes>0){
    clearTimeout(changeTimer);
    changeTimer=setTimeout(changeVideo,minutes*60000);
  }
}

// One failed playback attempt must only reject the clip and schedule a retry once.
// HTMLMediaElement.error and a rejected play() promise often report the same failure.
function reportPlaybackFailure(attempt,err){
  if(attempt!==playAttempt || failedAttempt===attempt || switchTimer!==null || !selectedFile)return;
  failedAttempt=attempt;
  cancelMediaTransition();
  videoPresented=false;
  document.body.classList.remove('media-transition');
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
function stopEmbeddedPlayer(){
  ++embeddedGeneration;
  clearTimeout(embeddedTimer);
  embeddedTimer=null;
  if(embeddedPlayer){
    try{embeddedPlayer.destroy();}catch(error){console.warn('External player cleanup failed',error);}
    embeddedPlayer=null;
  }
  // Recreate the element replaced by the iframe API on each playback.
  const layer=$('externalLayer');
  layer.innerHTML='<div id="externalPlayer"></div>';
  layer.hidden=true;
  embeddedActive=false;
}
function loadYouTubeAPI(){
  if(globalThis.YT?.Player)return Promise.resolve(globalThis.YT);
  if(embeddedApiPromise)return embeddedApiPromise;
  embeddedApiPromise=new Promise((resolve,reject)=>{
    globalThis.onYouTubeIframeAPIReady=()=>resolve(globalThis.YT);
    const script=document.createElement('script');
    script.src='https://www.youtube.com/iframe_api';
    script.onerror=()=>{
      embeddedApiPromise=null;
      reject(new Error('Не удалось загрузить плеер YouTube'));
    };
    document.head.appendChild(script);
  });
  return embeddedApiPromise;
}
function embeddedPlaybackFailed(generation,error){
  if(generation!==embeddedGeneration||!embeddedActive)return;
  console.error('Pi Aerial: external playback failed',error);
  changeVideo();
}
function playEmbedded(request){
  // Only provider-owned identifiers from the local API may reach a player.
  if(request.provider!=='youtube'||!/^[A-Za-z0-9_-]{11}$/.test(request.video_id)){
    console.error('Pi Aerial: invalid external playback request');
    changeVideo();
    return;
  }
  stopEmbeddedPlayer();
  cancelMediaTransition();
  ++playAttempt; // Invalidate callbacks from any interrupted local video.
  clearTimeout(changeTimer);
  selectedFile='youtube:'+request.video_id;
  selectedVideo=null;
  video.pause();
  video.muted=true;
  video.classList.remove('ready');
  videoPresented=false;
  if(video.hasAttribute('src')){video.removeAttribute('src');video.load();}
  document.body.classList.remove('media-transition');
  $('externalLayer').hidden=false;
  embeddedActive=true;
  const generation=embeddedGeneration;
  embeddedTimer=setTimeout(()=>embeddedPlaybackFailed(generation,new Error('YouTube startup timed out')),45000);
  loadYouTubeAPI().then(YT=>{
    if(generation!==embeddedGeneration||!playingOnce)return;
    embeddedPlayer=new YT.Player('externalPlayer',{
      width:'100%',height:'100%',videoId:request.video_id,
      playerVars:{autoplay:1,playsinline:1,rel:0,origin:location.origin},
      events:{
        onReady:event=>{
          if(generation!==embeddedGeneration||!playingOnce)return;
          event.target.unMute();
          event.target.playVideo();
        },
        onStateChange:event=>{
          if(generation!==embeddedGeneration||!playingOnce)return;
          if(event.data===YT.PlayerState.PLAYING){
            clearTimeout(embeddedTimer);
            embeddedTimer=null;
          }else if(event.data===YT.PlayerState.ENDED)changeVideo();
        },
        onError:event=>embeddedPlaybackFailed(generation,new Error('YouTube error '+event.data)),
        onAutoplayBlocked:()=>embeddedPlaybackFailed(generation,new Error('YouTube autoplay blocked'))
      }
    });
  }).catch(error=>embeddedPlaybackFailed(generation,error));
}
function loadVKAPI(){
  if(globalThis.VK?.VideoPlayer)return Promise.resolve(globalThis.VK);
  if(vkApiPromise)return vkApiPromise;
  vkApiPromise=new Promise((resolve,reject)=>{
    const script=document.createElement('script');
    script.src='https://vk.com/js/api/videoplayer.js';
    script.onload=()=>{
      if(globalThis.VK?.VideoPlayer)resolve(globalThis.VK);
      else reject(new Error('VK Video API unavailable'));
    };
    script.onerror=()=>reject(new Error('Не удалось загрузить плеер VK Видео'));
    document.head.appendChild(script);
  }).catch(error=>{
    vkApiPromise=null;
    throw error;
  });
  return vkApiPromise;
}
function validVKVideoRequest(request){
  return request.provider==='vkvideo' &&
    typeof request.owner_id==='string' && /^-?[0-9]{1,20}$/.test(request.owner_id) &&
    typeof request.video_id==='string' && /^[0-9]{1,20}$/.test(request.video_id) &&
    (request.hash===undefined ||
      (typeof request.hash==='string' && /^[a-fA-F0-9]{1,128}$/.test(request.hash)));
}
function playVKEmbedded(request){
  if(!validVKVideoRequest(request)){
    console.error('Pi Aerial: invalid VK Video request');
    changeVideo();
    return;
  }
  stopEmbeddedPlayer();
  cancelMediaTransition();
  ++playAttempt;
  clearTimeout(changeTimer);
  selectedFile='vkvideo:'+request.owner_id+'_'+request.video_id;
  selectedVideo=null;
  video.pause();
  video.muted=true;
  video.classList.remove('ready');
  videoPresented=false;
  if(video.hasAttribute('src')){video.removeAttribute('src');video.load();}
  document.body.classList.remove('media-transition');
  $('externalLayer').hidden=false;
  embeddedActive=true;
  const generation=embeddedGeneration;
  embeddedTimer=setTimeout(()=>embeddedPlaybackFailed(generation,new Error('VK Video startup timed out')),45000);
  loadVKAPI().then(VK=>{
    if(generation!==embeddedGeneration||!playingOnce)return;
    const iframe=document.createElement('iframe');
    iframe.id='externalVKPlayer';
    iframe.title='VK Видео';
    // Fixed VK host; all query values are revalidated, never inject raw URLs.
    iframe.src='https://vkvideo.ru/video_ext.php?oid='+encodeURIComponent(request.owner_id)+
      '&id='+encodeURIComponent(request.video_id)+'&hd=4&autoplay=1&js_api=1'+
      (request.hash?'&hash='+encodeURIComponent(request.hash):'');
    iframe.allow='autoplay; encrypted-media; fullscreen; picture-in-picture';
    iframe.allowFullscreen=true;
    iframe.referrerPolicy='strict-origin-when-cross-origin';
    $('externalLayer').replaceChildren(iframe);
    embeddedPlayer=VK.VideoPlayer(iframe);
    embeddedPlayer.on('inited',()=>{
      if(generation!==embeddedGeneration||!playingOnce)return;
      try{
        embeddedPlayer.unmute();
        const result=embeddedPlayer.play();
        if(result && typeof result.catch==='function'){
          result.catch(error=>embeddedPlaybackFailed(generation,error));
        }
      }catch(error){embeddedPlaybackFailed(generation,error);}
    });
    const started=()=>{
      if(generation!==embeddedGeneration||!playingOnce)return;
      clearTimeout(embeddedTimer);
      embeddedTimer=null;
    };
    embeddedPlayer.on('started',started);
    embeddedPlayer.on('resumed',started);
    embeddedPlayer.on('ended',()=>{
      if(generation===embeddedGeneration&&playingOnce)changeVideo();
    });
    embeddedPlayer.on('error',error=>embeddedPlaybackFailed(generation,
      new Error('VK Video error: '+String(error?.state||'unknown'))));
  }).catch(error=>embeddedPlaybackFailed(generation,error));
}
function changeVideo(){
  clearTimeout(changeTimer);
  if(embeddedActive)stopEmbeddedPlayer();
  // Return to silent screensaver mode immediately, including early stop/failure.
  video.muted=true;
  if(playingOnce){
    videoPresented=false;
    document.body.classList.add('media-transition');
    playingOnce=false;
    document.body.classList.remove('playing-once');
  }
  const now=Date.now();
  const candidates=videos.map((v,i)=>({v,i})).filter(x=>(rejectedUntil.get(x.v.id||x.v.url)||0)<now);
  if(!candidates.length){
    cancelMediaTransition();
    videoPresented=false;
    document.body.classList.remove('media-transition');
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

}
function playVideo(clip){
  if(embeddedActive)stopEmbeddedPlayer();
  const fadeOut=videoPresented&&!playingOnce;
  const attempt=++playAttempt;
  cancelMediaTransition();
  document.body.classList.add('media-transition');
  const start=()=>{
    switchTimer=null;
    if(attempt!==playAttempt)return;
    videoPresented=false;
    selectedFile=clip.id||clip.url;
    selectedVideo=clip;
    setText('clipName',clip.credit||displayText('ВИДЕОЗАСТАВКА','VIDEO SCREENSAVER'));
    renderClipDescription(0);
    video.classList.remove('ready');
    // Only an explicit "play once" command from the admin panel may use audio.
    // Set this before loading the source: transitions reuse the same <video>.
    video.muted=!playingOnce;
    video.src=clip.url;
    video.load();
    startupTimer=setTimeout(()=>reportPlaybackFailure(attempt,
      new Error('Время запуска видео истекло')),15000);
    video.play().then(()=>{
      if(attempt!==playAttempt || failedAttempt===attempt)return;
      if(typeof video.requestVideoFrameCallback==='function'){
        frameRequest=video.requestVideoFrameCallback(()=>finishMediaTransition(attempt));
      }else{
        // Older Chromium: fulfilled play() means playback has started.
        finishMediaTransition(attempt);
      }
    }).catch(err=>reportPlaybackFailure(attempt,err));
  };
  if(fadeOut)switchTimer=setTimeout(start,FADE_OUT_MS);
  else start();
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
    if(request.kind==='embed'&&request.provider==='vkvideo')playVKEmbedded(request);
    else if(request.kind==='embed')playEmbedded(request);
    else playVideo(request);
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
const osdBlocks={clock:'clockCard',weather:'weatherCard',calendar:'calendarCard',footer:'clipFooter'};
// Explicit font stacks avoid network font downloads and are safe on the Pi.
const osdFonts={
  default:'',
  arial:'Arial,"Liberation Sans",sans-serif',
  verdana:'Verdana,"DejaVu Sans",sans-serif',
  trebuchet:'"Trebuchet MS","DejaVu Sans",sans-serif',
  georgia:'Georgia,"DejaVu Serif",serif',
  times:'"Times New Roman","Liberation Serif",serif',
  courier:'"Courier New","Liberation Mono",monospace'
};
function osdFontStack(selection){
  if(Object.prototype.hasOwnProperty.call(osdFonts,selection))return osdFonts[selection];
  if(typeof selection==='string'&&selection.startsWith('installed:')){
    const family=selection.slice('installed:'.length);
    if(family&&family.length<=160&&[...family].every(ch=>ch.charCodeAt(0)>31&&ch.charCodeAt(0)!==127)){
      // JSON quoting also escapes literal quote/backslash characters for CSS.
      return JSON.stringify(family)+',sans-serif';
    }
  }
  return '';
}
function applyOsdLayout(layout){
  const custom=Boolean(layout&&Object.keys(layout).length);
  document.body.classList[custom?'add':'remove']('custom-osd-layout');
  for(const [name,id] of Object.entries(osdBlocks)){
    const node=$(id),geometry=custom?layout[name]:null;
    if(!node)continue;
    node.style.left=geometry?geometry.x+'%':'';
    node.style.top=geometry?geometry.y+'%':'';
    node.style.right=geometry?'auto':'';
    node.style.bottom=geometry?'auto':'';
    node.style.transform=geometry?'scale('+geometry.scale+')':'';
    const font=geometry?.font;
    node.style.fontFamily=osdFontStack(font);
    node.style.color=geometry?.color||'';
    // Descendant labels otherwise have hard-coded colors; preserve their
    // existing defaults unless an explicit color is selected for this block.
    if(geometry?.color)node.style.setProperty('--osd-text-color',geometry.color);
    else node.style.removeProperty('--osd-text-color');
  }
}
function setOsdSideClasses(right){
  const sideClass=right?' osd-right':'';
  const calendarClass='calendar-card'+(!right?' osd-right':'');
  if($('calendarCard').className!==calendarClass)$('calendarCard').className=calendarClass;
  for(const [id,base] of [['clockCard','clock-card'],['weatherCard','weather-card']]){
    const name=base+sideClass;
    if($(id).className!==name)$(id).className=name;
  }
  const footerClass='footer'+sideClass;
  if($('clipFooter').className!==footerClass)$('clipFooter').className=footerClass;
}
// The editor uses this to preview either of the original side presets without
// modifying persisted settings or starting another video player.
function previewOsdPreset(index){
  if(!layoutPreview)return;
  setOsdSideClasses(index===1);
  applyOsdLayout({});
}
function renderOsdPosition(){
  const settings=state?.settings||{};
  const second=settings.osd_layout_mode!=='single'&&osdRight;
  const layout=(second?settings.osd_layout_alt:settings.osd_layout)||{};
  const custom=Boolean(Object.keys(layout).length);
  setOsdSideClasses(second&&!custom);
  applyOsdLayout(layout);
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
    if(!layoutPreview&&!playingOnce&&(first||shouldChange))changeVideo();
    if(!layoutPreview&&(first||oldIds!==newIds))renderModules();
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
video.addEventListener('ended',()=>{if(switchTimer===null&&!embeddedActive)changeVideo();});
video.addEventListener('error',()=>{if(!embeddedActive)reportPlaybackFailure(playAttempt,video.error);});
setInterval(drawTime,10000);
if(!layoutPreview){
  setInterval(poll,30000);
  setInterval(pollDisplayRequest,3000);
}
poll();
