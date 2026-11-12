'use strict';
const $=id=>document.getElementById(id);
let csrf='',status=null;
function toast(text){const n=$('notice');n.textContent=PiI18n.translate(text);n.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>n.classList.remove('show'),5000)}
function bytes(n){if(n>1024**3)return(n/1024**3).toFixed(1)+' ГБ';return(n/1024**2).toFixed(1)+' МБ'}
async function api(path,{method='GET',body=null,upload=false}={}){const opts={method,credentials:'same-origin',headers:{}};if(method!=='GET')opts.headers['x-csrf-token']=csrf;if(body){if(upload)opts.body=body;else{opts.headers['content-type']='application/json';opts.body=JSON.stringify(body)}}const r=await fetch(path,opts);const data=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:`Ошибка HTTP ${r.status}`);return data;}
function togglePanel(logged){$('panel').hidden=!logged;$('loginView').hidden=logged;$('logout').hidden=!logged;$('connection').textContent=logged?'Подключено':'Нет входа';}
let pendingLanguage=null;
async function init(){
  try{
    const pub=await api('/api/public');
    const language=pendingLanguage||pub.settings?.language||'ru';
    PiI18n.setLocale(language);
    $('languageSelect').value=language;
  }catch(e){console.warn('Could not detect display language',e)}
  try{
    const r=await api('/api/auth/session');
    csrf=r.csrf;
    togglePanel(true);
    await PiLocationPicker.loadTimezones();
    await load();
  }catch{togglePanel(false)}
}
async function load(){status=await api('/api/admin/status');$('version').textContent='v'+status.version;$('countVideos').textContent=status.videos.length;$('freeDisk').textContent=bytes(status.storage.free);$('diskTotal').textContent='Объём '+bytes(status.storage.total);$('moduleCount').textContent=status.modules.length;$('keyCount').textContent=status.trusted_keys;$('updateState').textContent=status.update_waiting?'Ожидание завершения загрузки и конвертации':status.update_pending?'Идёт установка':(status.last_update?.message||'Нет обновлений');renderConfig();renderVideos();renderModules();renderOnlineSources();PiI18n.apply($('panel'));}
function renderConfig(){const f=$('settingsForm'),a=$('autoUpdateForm'),c=status.config;for(const [key,value] of Object.entries(c)){const field=f.elements[key]||a.elements[key];if(!field)continue;if(field.type==='checkbox')field.checked=!!value;else if(key==='night_opacity')field.value=Math.round(value*100);else field.value=String(value);}updateNightValue();PiLocationPicker.sync(c);if(!pendingLanguage&&PiI18n.language!==c.language){PiI18n.setLocale(c.language||'ru');$('languageSelect').value=c.language||'ru';}}
function updateNightValue(){$('nightValue').textContent=$('settingsForm').elements.night_opacity.value+'%'}
function textNode(tag,text,cls){const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;}
function line(name,desc){const row=document.createElement('div');row.className='line';const t=document.createElement('div');t.append(textNode('strong',name),textNode('small',desc));row.append(t);return row;}
function progressSize(n){
  const value=Math.max(0,Number(n)||0);
  if(value<1024*1024)return (value/1024).toFixed(0)+' КБ';
  if(value<1024**3)return (value/1024**2).toFixed(1)+' МБ';
  return (value/1024**3).toFixed(2)+' ГБ';
}
function progressTime(n){
  if(n===null||n===undefined||!Number.isFinite(Number(n)))return 'неизвестно';
  const t=Math.max(0,Math.floor(Number(n)));
  return String(Math.floor(t/60)).padStart(2,'0')+':'+String(t%60).padStart(2,'0');
}
function renderVideoProgress(data){
  const root=$('videoProgressList'),recent=$('videoProgressRecent');
  root.replaceChildren();
  const stageLabels={
    starting:'Соединение с сервером',downloading:'Скачивание',
    waiting:'Ожидание свободного конвертера',converting:'Подготовка MP4',
    finalizing:'Проверка готового файла'
  };
  const active=Array.isArray(data.active)?data.active:[];
  if(!active.length)root.append(textNode('p','Сейчас загрузка и обработка не выполняются.','hint'));
  for(const job of active){
    const item=document.createElement('div');item.className='video-progress-item';
    const source=job.kind==='local'?'Загруженный файл':(job.provider||'Онлайн');
    item.append(textNode('strong',source+' · '+job.name));
    const percent=typeof job.percent==='number'&&Number.isFinite(job.percent)?job.percent:null;
    let detail=stageLabels[job.stage]||job.stage;
    if(job.stage==='downloading'){
      detail+=' · '+progressSize(job.bytes_received);
      detail+=job.bytes_total?' из '+progressSize(job.bytes_total):' · размер неизвестен';
      detail+=' · '+progressSize(job.speed_bps)+'/с';
    }else if(job.stage==='converting'){
      detail+=' · '+(job.mode==='copy'?'перепаковка без потерь':'H.264, перекодирование');
      detail+=' · '+progressTime(job.position_s);
      detail+=job.duration_s?' / '+progressTime(job.duration_s):' · длительность неизвестна';
      if(job.encode_speed)detail+=' · '+job.encode_speed;
    }
    detail+=' · прошло '+progressTime(job.elapsed_s);
    if(percent!==null)detail+=' · '+percent.toFixed(1)+'%';
    item.append(textNode('small',detail));
    const bar=document.createElement('progress');bar.max=100;
    if(percent!==null)bar.value=Math.min(100,Math.max(0,percent));
    bar.setAttribute('aria-label',source+': '+(stageLabels[job.stage]||job.stage));
    item.append(bar);root.append(item);
  }
  const done=Array.isArray(data.recent)?data.recent:[];
  recent.textContent=done.length?
    'Недавние задания: '+done.map(j=>(j.stage==='ready'?'Готово':'Ошибка')+' · '+j.name).join(' | '):'';
}
let videoProgressBusy=false;
async function refreshVideoProgress(){
  if(videoProgressBusy)return;
  videoProgressBusy=true;
  try{
    const result=await api('/api/admin/videos/progress');
    renderVideoProgress(result);
  }finally{videoProgressBusy=false}
}
function renderVideos(){
  const list=$('videoList');list.replaceChildren();
  for(const v of status.videos){
    const row=line(v.name,bytes(v.bytes)+' · готов к воспроизведению');
    const controls=document.createElement('div');controls.className='inline-actions';
    const remove=textNode('button','Удалить');
    remove.addEventListener('click',async()=>{
      if(!ask('Удалить готовое видео с карты памяти?'))return;
      try{
        await api('/api/admin/videos/'+encodeURIComponent(v.name),{method:'DELETE'});
        await load();toast('Видео удалено');
      }catch(e){toast(e.message)}
    });
    controls.append(remove);row.append(controls);list.append(row);
  }
  const labels={queued:'В очереди',running:'Фоновая конвертация',failed:'Не удалось обработать'};
  for(const job of status.video_processing||[]){
    const description=(labels[job.state]||job.state)+
      (job.error?' · '+job.error:'')+
      (job.origin==='videos'?' · ранее загруженный файл':'');
    const row=line(job.name,description);
    const controls=document.createElement('div');controls.className='inline-actions';
    if(job.state==='failed'){
      const retry=textNode('button','Повторить');
      retry.addEventListener('click',async()=>{
        retry.disabled=true;
        try{
          await api('/api/admin/videos/jobs/'+job.id+'/retry',{method:'POST'});
          await load();toast('Файл снова в очереди');
        }catch(e){toast(e.message);retry.disabled=false}
      });
      controls.append(retry);
    }
    if(job.state!=='running'){
      const remove=textNode('button','Удалить исходник');
      remove.addEventListener('click',async()=>{
        if(!ask('Удалить исходное видео с карты памяти?'))return;
        try{
          await api('/api/admin/videos/jobs/'+job.id,{method:'DELETE'});
          await load();toast('Исходный файл удалён');
        }catch(e){toast(e.message)}
      });
      controls.append(remove);
    }
    row.append(controls);list.append(row);
  }
  if(!list.children.length)list.append(textNode('p','Загруженных файлов пока нет'));
}
function renderOnlineSources(){
  const root=$('onlineSourceList');root.replaceChildren();

  const localRow=line('Загруженные файлы',
    `${status.videos.length} готово · ${(status.video_processing||[]).length} в очереди/обработке · не онлайн-кеш`);
  const localLabel=document.createElement('label');
  const localCheck=document.createElement('input');localCheck.type='checkbox';
  localCheck.checked=status.config.local_videos_enabled!==false;
  localCheck.title='Показывать загруженные через веб-интерфейс видео';
  localCheck.addEventListener('change',async()=>{
    const enabled=localCheck.checked;localCheck.disabled=true;
    try{
      await api('/api/admin/config',{method:'PUT',
        body:{changes:{local_videos_enabled:enabled}}});
      await load();toast(enabled?'Локальные видео включены':'Локальные видео отключены из показа');
    }catch(e){localCheck.checked=!enabled;toast(e.message)}
    finally{localCheck.disabled=false}
  });
  localLabel.append(localCheck,document.createTextNode(' Включён'));
  localRow.append(localLabel);root.append(localRow);

  const config=status.config.online_sources||{};
  for(const source of status.online_sources||[]){
    const msg=source.error||'Каталог готов';
    const preparation=['apple','nasa','robin'].includes(source.id)?` · готово к показу: ${source.playable||0}`:'';
    const row=line(source.name,`${source.count} роликов${preparation} · ${msg}`);
    const label=document.createElement('label');
    const check=document.createElement('input');check.type='checkbox';
    check.checked=!!config[source.id];check.title='Включить источник '+source.name;
    check.addEventListener('change',async()=>{
      check.disabled=true;
      const next={...config,[source.id]:check.checked};
      try{
        await api('/api/admin/config',{method:'PUT',body:{changes:{online_sources:next}}});
        await load();toast('Источник обновлён. Каталог загрузится в фоне.');
      }catch(e){check.checked=!check.checked;toast(e.message)}
      finally{check.disabled=false}
    });
    label.append(check,document.createTextNode(' Включён'));
    row.append(label);root.append(row);
  }
  $('onlineAutodownload').checked=status.config.online_autodownload_enabled!==false;
  $('onlineCacheEnabled').checked=!!status.cache?.enabled;
  $('onlineCacheLimit').value=Number(status.cache?.limit_gb||12);
  $('onlineCacheLimit').max=Number(status.cache?.max_limit_gb||12);
  const cache=status.cache||{};
  const used=(Number(cache.used_bytes||0)/1073741824).toFixed(2);
  const limit=Number(cache.limit_gb||12);
  const state=!cache.enabled?'Выключена':cache.paused
    ?(cache.pause_reason==='limit'?'Пауза: достигнут лимит кеша':'Пауза: мало свободного места')
    :status.config.online_autodownload_enabled===false?'Автозагрузка выключена':'Загрузка разрешена';
  $('onlineCacheState').textContent=`Использовано ${used} из ${limit} ГБ · ${state}. Готовые ролики сохраняются.`;
}
let libraryLoading=false;
async function loadLibrary(){
  if(libraryLoading)return;
  libraryLoading=true;
  try{
    const data=await api('/api/admin/online/library');
    renderLibrary(Array.isArray(data.videos)?data.videos:[]);
  }catch(e){
    $('librarySummary').textContent='Не удалось получить список: '+e.message;
    toast(e.message);
  }finally{libraryLoading=false}
}
function openLibraryPreview(title,url){
  const dialog=$('libraryPreviewDialog'),player=$('libraryPreviewPlayer');
  $('libraryPreviewTitle').textContent=title;
  // Preview is an explicit administrator action, unlike silent screen rotation.
  player.muted=false;
  player.src=url;
  dialog.showModal();
  player.play().catch(()=>{});
}
function playOnceButton(source,name){
  const button=textNode('button','Показать один раз без OSD');
  button.addEventListener('click',async()=>{
    button.disabled=true;
    try{
      await api('/api/admin/display/play-once/'+source+'/'+encodeURIComponent(name),{method:'POST'});
      toast('Запрос отправлен на экран заставки');
    }catch(e){toast(e.message)}
    finally{button.disabled=false}
  });
  return button;
}
let libraryVideos=[];
let librarySelectedSources=null;
let libraryKnownSources=new Set();

function activeLibrarySources(){
  const enabled=status?.config?.online_sources||{};
  const sources=(status?.online_sources||[])
    .filter(source=>source&&typeof source.id==='string'&&enabled[source.id]===true)
    .map(source=>({id:source.id,name:source.name||source.id}));
  if(status?.config?.local_videos_enabled!==false)
    sources.push({id:'local',name:'Загруженные файлы'});
  return sources;
}
function renderLibrarySourceFilters(onlineVideos){
  const sources=activeLibrarySources();
  const available=new Set(sources.map(source=>source.id));
  if(librarySelectedSources===null)librarySelectedSources=new Set(available);
  else{
    for(const id of librarySelectedSources)
      if(!available.has(id))librarySelectedSources.delete(id);
    for(const id of available)
      if(!libraryKnownSources.has(id))librarySelectedSources.add(id);
  }
  libraryKnownSources=available;
  const root=$('librarySourceFilters');
  root.replaceChildren();
  const all=textNode('button',PiI18n.translate('Все источники'));
  all.type='button';
  all.disabled=available.size===0||librarySelectedSources.size===available.size;
  all.addEventListener('click',()=>{
    librarySelectedSources=new Set(available);
    renderLibrary(libraryVideos);
  });
  root.append(all);
  for(const source of sources){
    const count=source.id==='local'?(status?.videos||[]).length:
      onlineVideos.filter(video=>video.provider===source.id).length;
    const label=document.createElement('label');
    const check=document.createElement('input');
    check.type='checkbox';
    check.checked=librarySelectedSources.has(source.id);
    check.addEventListener('change',()=>{
      if(check.checked)librarySelectedSources.add(source.id);
      else librarySelectedSources.delete(source.id);
      renderLibrary(libraryVideos);
    });
    label.append(check,document.createTextNode(
      PiI18n.translate(source.name)+' ('+count+')'));
    root.append(label);
  }
  $('libraryFilterHint').textContent=PiI18n.translate(
    available.size===0?'Нет включённых источников.':
      librarySelectedSources.size===0?'Выберите хотя бы один источник.':'');
}

function renderLibrary(onlineVideos){
  libraryVideos=onlineVideos;
  renderLibrarySourceFilters(onlineVideos);
  const filteredOnline=onlineVideos.filter(video=>librarySelectedSources.has(video.provider));
  const filteredLocal=librarySelectedSources.has('local')?(status?.videos||[]):[];
  $('onlineLibraryCard').hidden=!activeLibrarySources().some(source=>
    source.id!=='local'&&librarySelectedSources.has(source.id));
  $('localLibraryCard').hidden=!librarySelectedSources.has('local');
  const onlineRoot=$('onlineLibraryList'),localRoot=$('localLibraryList');
  onlineRoot.replaceChildren();
  localRoot.replaceChildren();
  const ready=filteredOnline.filter(v=>v.cached);
  const deleted=filteredOnline.filter(v=>!v.cached);
  $('librarySummary').textContent=
    `Готово: ${ready.length} · удалено, но сохранено в истории: ${deleted.length} · всего на диске: ${progressSize(ready.reduce((sum,v)=>sum+Number(v.bytes||0),0))}`;
  for(const v of filteredOnline){
    const provider=v.provider==='apple'?'Apple Aerial':
      v.provider==='nasa'?'NASA':
      v.provider==='robin'?'Robin Fourcade':v.provider;
    const state=v.cached?'Готов к просмотру · '+progressSize(v.bytes):
      'MP4 удалён · повторная загрузка заблокирована';
    const english=PiI18n.language==='en';
    const name=english?(v.name_en||v.name):v.name;
    const description=english?(v.description_en||v.description):v.description;
    const row=line(name,provider+' · '+state);
    if(description)row.firstChild.append(textNode('p',description,'library-description'));
    const controls=document.createElement('div');controls.className='inline-actions library-actions';
    if(v.cached){
      const preview=textNode('button','Просмотр');
      preview.addEventListener('click',()=>openLibraryPreview(
        name,'/api/admin/online/library/'+encodeURIComponent(v.id)+'/preview'));
      const remove=textNode('button','Удалить файл');
      remove.addEventListener('click',async()=>{
        if(!ask('Удалить готовый MP4? Запрет повторной загрузки останется.'))return;
        remove.disabled=true;
        try{
          await api('/api/admin/online/library/'+encodeURIComponent(v.id),{method:'DELETE'});
          toast('Файл удалён. Повторная загрузка по-прежнему заблокирована.');
          await load();await loadLibrary();
        }catch(e){toast(e.message);remove.disabled=false}
      });
      controls.append(preview,playOnceButton('online',v.id),remove);
    }else{
      if(v.can_redownload){
        const unlock=textNode('button','Разрешить повторную загрузку');
        unlock.addEventListener('click',async()=>{
          if(!ask('Снять блокировку и разрешить снова скачать этот ролик?'))return;
          unlock.disabled=true;
          try{
            await api('/api/admin/online/library/'+encodeURIComponent(v.id)+'/redownload',{method:'POST'});
            toast('Блокировка снята. Загрузка начнётся по общей очереди.');
            await load();await loadLibrary();
          }catch(e){toast(e.message);unlock.disabled=false}
        });
        controls.append(unlock);
      }else{
        controls.append(textNode('small','Ролика уже нет в текущем каталоге'));
      }
    }
    row.append(controls);onlineRoot.append(row);
  }
  if(!filteredOnline.length)onlineRoot.append(textNode('p','Пока нет скачанных роликов.','hint'));
  for(const v of filteredLocal){
    const row=line(v.name,'Локальная загрузка · '+progressSize(v.bytes));
    const controls=document.createElement('div');controls.className='inline-actions library-actions';
    const preview=textNode('button','Просмотр');
    preview.addEventListener('click',()=>openLibraryPreview(v.name,v.url));
    const remove=textNode('button','Удалить файл');
    remove.addEventListener('click',async()=>{
      if(!ask('Удалить загруженный вами файл с карты?'))return;
      remove.disabled=true;
      try{
        await api('/api/admin/videos/'+encodeURIComponent(v.name),{method:'DELETE'});
        toast('Файл удалён');await load();await loadLibrary();
      }catch(e){toast(e.message);remove.disabled=false}
    });
    controls.append(preview,playOnceButton('local',v.name),remove);row.append(controls);localRoot.append(row);
  }
  if(!filteredLocal.length)localRoot.append(textNode('p','Своих готовых файлов пока нет.','hint'));
}
async function loadPlaybackStatus(){
  try{
    const data=await api('/api/admin/display/play-once/status');
    const info=data.status;
    const stateLabels={
      requested:'Команда отправлена, ждём экран',
      starting:'Плеер запускается',
      api_ready:'API плеера загружен, ждём видео',
      playing:'Видео воспроизводится',
      ended:'Видео завершилось, заставка восстановлена',
      stopped:'Воспроизведение остановлено',
      error:'Ошибка воспроизведения'
    };
    const node=$('externalVideoStatus');
    if(!info){
      node.textContent=PiI18n.translate('Статус: запусков по ссылке пока не было');
      return;
    }
    const provider=info.provider==='youtube'?'YouTube':info.provider==='vkvideo'?'VK Видео':info.provider;
    const message=stateLabels[info.stage]||info.stage;
    const extra=info.detail?' · '+info.detail:'';
    node.textContent=provider+' · '+PiI18n.translate(message)+extra;
  }catch(error){
    $('externalVideoStatus').textContent=PiI18n.translate('Статус плеера недоступен')+' · '+error.message;
  }
}
$('externalVideoForm').addEventListener('submit',async event=>{
  event.preventDefault();
  const form=event.currentTarget,input=$('externalVideoUrl');
  const url=input.value.trim();
  if(!url)return;
  setBusy(form,true);
  try{
    await api('/api/admin/display/play-once/url',{method:'POST',body:{url}});
    toast('Запрос отправлен на экран заставки');
    await loadPlaybackStatus();
  }catch(error){toast(error.message)}
  finally{setBusy(form,false)}
});
$('libraryRefresh').addEventListener('click',async()=>{await load();await loadLibrary();await loadPlaybackStatus()});
$('stopDisplayVideo').addEventListener('click',async e=>{
  const button=e.currentTarget;
  button.disabled=true;
  try{
    await api('/api/admin/display/play-once/stop',{method:'POST'});
    toast('Команда остановки отправлена');
    await loadPlaybackStatus();
  }catch(e){toast(e.message)}
  finally{button.disabled=false}
});
$('libraryPreviewClose').addEventListener('click',()=>$('libraryPreviewDialog').close());
$('libraryPreviewDialog').addEventListener('close',()=>{
  const player=$('libraryPreviewPlayer');
  player.pause();player.removeAttribute('src');player.load();
});

function renderModules(){const list=$('moduleList');list.replaceChildren();for(const m of status.modules){const row=line(m.name||m.id,`${m.id} · v${m.version} · ${m.position||'top-right'}`);const controls=document.createElement('div');controls.className='inline-actions';const check=document.createElement('input');check.type='checkbox';check.checked=m.enabled;check.title='Модуль включён';check.addEventListener('change',async()=>{try{await api('/api/admin/modules/'+m.id+'/enabled',{method:'PUT',body:{enabled:check.checked}});toast('Настройка сохранена')}catch(e){check.checked=!check.checked;toast(e.message)}});controls.append(check);if(m.rollback_available){const back=textNode('button','Откатить');back.addEventListener('click',async()=>{if(!ask('Вернуть предыдущую версию модуля?'))return;try{await api('/api/admin/modules/'+m.id+'/rollback',{method:'POST'});await load();toast('Модуль откатан')}catch(e){toast(e.message)}});controls.append(back)}row.append(controls);list.append(row);}}
function ask(message){return confirm(PiI18n.translate(message));}
function setBusy(form,busy){const b=form.querySelector('button');b.disabled=busy;b.dataset.old=b.dataset.old||b.textContent;b.textContent=busy?'Обработка…':b.dataset.old;}
async function upload(formId,inputId,url){const form=$(formId);const file=$(inputId).files[0];if(!file)return;const data=new FormData();data.append('file',file);setBusy(form,true);try{const res=await api(url,{method:'POST',body:data,upload:true});toast(res.message||'Файл принят');form.reset();await load();}catch(e){toast(e.message)}finally{setBusy(form,false)}}
$('loginForm').addEventListener('submit',async e=>{e.preventDefault();try{const a=await api('/api/auth/login',{method:'POST',body:{password:$('password').value}});csrf=a.csrf;$('password').value='';$('loginError').textContent='';togglePanel(true);if(pendingLanguage){await api('/api/admin/config',{method:'PUT',body:{changes:{language:pendingLanguage}}});pendingLanguage=null;}await PiLocationPicker.loadTimezones();await load();}catch(err){$('loginError').textContent=err.message;}});
$('logout').addEventListener('click',async()=>{try{await api('/api/auth/logout',{method:'POST'});}finally{csrf='';togglePanel(false)}});
document.querySelectorAll('.tab').forEach(tab=>tab.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===tab));document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id==='tab-'+tab.dataset.tab));if(tab.dataset.tab==='layout')PiLayoutEditor.open({status,api,toast,load});if(tab.dataset.tab==='videos')refreshVideoProgress().catch(e=>console.warn('Прогресс видео:',e));if(tab.dataset.tab==='library')load().then(loadLibrary).then(loadPlaybackStatus).catch(e=>toast(e.message));}));
$('settingsForm').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget;const changes={};for(const name of ['show_clock','show_date','show_weather','night_mode'])changes[name]=f.elements[name].checked;for(const name of ['city','timezone','time_format','video_mode'])changes[name]=f.elements[name].value;for(const name of ['latitude','longitude','night_opacity'])changes[name]=Number(f.elements[name].value);for(const name of ['video_interval_minutes','night_start','night_end'])changes[name]=Number(f.elements[name].value);changes.night_opacity/=100;try{await api('/api/admin/config',{method:'PUT',body:{changes}});$('settingsMessage').textContent='Сохранено';toast('Настройки применены');await load();}catch(e){toast(e.message)}});
$('autoUpdateForm').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget;const interval=Number(f.elements.auto_update_interval_minutes.value);if(!Number.isInteger(interval)||interval<1){toast('Интервал автообновления должен быть целым числом минут не меньше 1');return;}const changes={auto_update_enabled:f.elements.auto_update_enabled.checked,auto_update_interval_minutes:interval};try{await api('/api/admin/config',{method:'PUT',body:{changes}});$('autoUpdateMessage').textContent='Сохранено';toast('Настройки применены');await load();}catch(e){toast(e.message)}});
$('settingsForm').elements.night_opacity.addEventListener('input',updateNightValue);
PiI18n.observe();
PiLocationPicker.init(api);
$('languageSelect').addEventListener('change',async()=>{
  const language=$('languageSelect').value==='en'?'en':'ru';
  PiI18n.setLocale(language);
  if(status)renderLibrary(libraryVideos);
  if(!csrf){pendingLanguage=language;return;}
  try{
    await api('/api/admin/config',{method:'PUT',body:{changes:{language}}});
    if(status)status.config.language=language;
    pendingLanguage=null;
  }catch(error){
    toast(error.message);
    if(status){
      $('languageSelect').value=status.config.language||'ru';
      PiI18n.setLocale(status.config.language||'ru');
      renderLibrary(libraryVideos);
    }
  }
});
$('reloadDisplay').addEventListener('click',async()=>{try{await api('/api/admin/display/reload',{method:'POST'});toast('Экран перезагрузится при следующей проверке')}catch(e){toast(e.message)}});
$('videoForm').addEventListener('submit',e=>{e.preventDefault();upload('videoForm','videoInput','/api/admin/videos')});
$('onlineRefresh').addEventListener('click',async()=>{
  const button=$('onlineRefresh');
  button.disabled=true;
  $('onlineSourceMessage').textContent='Проверяю Apple, NASA и Robin Fourcade…';
  try{
    await api('/api/admin/online/refresh',{method:'POST'});
    await load();
    $('onlineSourceMessage').textContent='Проверка завершена';
    toast('Каталоги Apple/NASA/Robin перепроверены');
  }catch(e){
    $('onlineSourceMessage').textContent=e.message;
    toast(e.message);
  }finally{button.disabled=false}
});
$('onlineCacheSave').addEventListener('click',async()=>{
  const cache_gb=Number($('onlineCacheLimit').value);
  const maximum=Number(status.cache?.max_limit_gb||12);
  if(!Number.isInteger(cache_gb)||cache_gb<1||cache_gb>maximum){toast('Размер кеша: от 1 до '+maximum+' ГБ');return;}
  try{
    await api('/api/admin/config',{method:'PUT',body:{changes:{
      online_cache_enabled:$('onlineCacheEnabled').checked,online_cache_gb:cache_gb
    }}});
    await load();toast('Настройки кеша сохранены');
  }catch(e){toast(e.message)}
});
$('onlineCacheClear').addEventListener('click',async()=>{
  if(!ask('Удалить сохранённые копии онлайн-роликов? Свои видео останутся.'))return;
  try{const res=await api('/api/admin/online/cache/clear',{method:'POST'});
    toast('Удалено файлов кеша: '+res.deleted);await load();
  }catch(e){toast(e.message)}
});
$('moduleForm').addEventListener('submit',e=>{e.preventDefault();upload('moduleForm','moduleInput','/api/admin/modules/install')});
$('coreForm').addEventListener('submit',e=>{e.preventDefault();upload('coreForm','coreInput','/api/admin/core/install')});
init();

async function checkGitHub(){
  const state=$('githubState');state.textContent='Проверяю GitHub…';
  $('githubInstall').disabled=true;
  try{const r=await api('/api/admin/github/status');
    state.textContent=`Установлено: ${r.installed?.slice(0,12)||'неизвестно'} · GitHub main: ${r.latest.slice(0,12)} · ${r.update_available?'Есть обновление':'Актуальная версия'}`;
    $('githubInstall').disabled=!r.update_available;
  }catch(e){state.textContent=e.message;toast(e.message)}
}
$('githubCheck').addEventListener('click',checkGitHub);
$('githubInstall').addEventListener('click',async()=>{
  if(!ask('Установить новую версию из GitHub? При ошибке запуска будет откат.'))return;
  try{const r=await api('/api/admin/github/update',{method:'POST'});toast(r.message);$('githubInstall').disabled=true;await load();}
  catch(e){toast(e.message)}
});
$('githubModulesCheck').addEventListener('click',async()=>{
  const target=$('githubModulesList');target.replaceChildren(textNode('p','Загружаю каталог…'));
  try{const r=await api('/api/admin/github/modules');target.replaceChildren();
    if(!r.modules.length)target.append(textNode('p','В GitHub пока нет модулей'));
    for(const m of r.modules){const row=line(m.name,`${m.id} · v${m.version} · установлено: ${m.installed_version||'нет'}`);
      const button=textNode('button',m.installed_version===m.version?'Переустановить':'Установить / обновить');
      button.addEventListener('click',async()=>{button.disabled=true;try{const out=await api('/api/admin/github/modules/'+encodeURIComponent(m.id)+'/install',{method:'POST'});toast(out.message);await load();}catch(e){toast(e.message)}finally{button.disabled=false}});
      row.append(button);target.append(row);
    }
  }catch(e){target.replaceChildren(textNode('p',e.message));toast(e.message)}
});

setInterval(()=>{
  if(!$('panel').hidden && document.querySelector('.tab.active')?.dataset.tab==='videos'){
    load().catch(err=>console.warn('Не удалось обновить статус видео:',err));
  }
},10000);

setInterval(()=>{
  if(!$('panel').hidden && document.querySelector('.tab.active')?.dataset.tab==='videos'){
    refreshVideoProgress().catch(e=>console.warn('Прогресс видео:',e));
  }
},2000);
setInterval(()=>{
  if(!$('panel').hidden && document.querySelector('.tab.active')?.dataset.tab==='library'){
    load().then(loadLibrary).catch(e=>console.warn('Список готовых видео:',e));
  }
},15000);
setInterval(()=>{
  if(!$('panel').hidden && document.querySelector('.tab.active')?.dataset.tab==='library'){
    loadPlaybackStatus();
  }
},3000);

$('onlineAutodownload').addEventListener('change',async e=>{
  const check=e.currentTarget;
  const enabled=check.checked;
  check.disabled=true;
  try{
    await api('/api/admin/config',{method:'PUT',body:{changes:{online_autodownload_enabled:enabled}}});
  }catch(error){
    check.checked=!enabled;
    toast(error.message);
    return;
  }finally{check.disabled=false}
  toast(enabled?'Автозагрузка включена':'Автозагрузка выключена');
  await load();
});
