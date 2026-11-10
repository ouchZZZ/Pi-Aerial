'use strict';
const $=id=>document.getElementById(id);
let csrf='',status=null;
function toast(text){const n=$('notice');n.textContent=text;n.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>n.classList.remove('show'),5000)}
function bytes(n){if(n>1024**3)return(n/1024**3).toFixed(1)+' ГБ';return(n/1024**2).toFixed(1)+' МБ'}
async function api(path,{method='GET',body=null,upload=false}={}){const opts={method,credentials:'same-origin',headers:{}};if(method!=='GET')opts.headers['x-csrf-token']=csrf;if(body){if(upload)opts.body=body;else{opts.headers['content-type']='application/json';opts.body=JSON.stringify(body)}}const r=await fetch(path,opts);const data=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:`Ошибка HTTP ${r.status}`);return data;}
function togglePanel(logged){$('panel').hidden=!logged;$('loginView').hidden=logged;$('logout').hidden=!logged;$('connection').textContent=logged?'Подключено':'Нет входа';}
async function init(){try{const r=await api('/api/auth/session');csrf=r.csrf;togglePanel(true);await load();}catch{togglePanel(false)}}
async function load(){status=await api('/api/admin/status');$('version').textContent='v'+status.version;$('countVideos').textContent=status.videos.length;$('freeDisk').textContent=bytes(status.storage.free);$('diskTotal').textContent='Объём '+bytes(status.storage.total);$('moduleCount').textContent=status.modules.length;$('keyCount').textContent=status.trusted_keys;$('updateState').textContent=status.update_pending?'Идёт установка':(status.last_update?.message||'Нет обновлений');renderConfig();renderVideos();renderModules();}
function renderConfig(){const f=$('settingsForm'),c=status.config;for(const [key,value] of Object.entries(c)){if(!f.elements[key])continue;if(f.elements[key].type==='checkbox')f.elements[key].checked=!!value;else if(key==='night_opacity')f.elements[key].value=Math.round(value*100);else f.elements[key].value=String(value);}updateNightValue();}
function updateNightValue(){$('nightValue').textContent=$('settingsForm').elements.night_opacity.value+'%'}
function textNode(tag,text,cls){const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;}
function line(name,desc){const row=document.createElement('div');row.className='line';const t=document.createElement('div');t.append(textNode('strong',name),textNode('small',desc));row.append(t);return row;}
function renderVideos(){const list=$('videoList');list.replaceChildren();for(const v of status.videos){const row=line(v.name,bytes(v.bytes));const controls=document.createElement('div');controls.className='inline-actions';const remove=textNode('button','Удалить');remove.addEventListener('click',async()=>{if(!confirm('Удалить видео с карты памяти?'))return;try{await api('/api/admin/videos/'+encodeURIComponent(v.name),{method:'DELETE'});await load();toast('Видео удалено')}catch(e){toast(e.message)}});controls.append(remove);row.append(controls);list.append(row);}}
function renderModules(){const list=$('moduleList');list.replaceChildren();for(const m of status.modules){const row=line(m.name||m.id,`${m.id} · v${m.version} · ${m.position||'top-right'}`);const controls=document.createElement('div');controls.className='inline-actions';const check=document.createElement('input');check.type='checkbox';check.checked=m.enabled;check.title='Модуль включён';check.addEventListener('change',async()=>{try{await api('/api/admin/modules/'+m.id+'/enabled',{method:'PUT',body:{enabled:check.checked}});toast('Настройка сохранена')}catch(e){check.checked=!check.checked;toast(e.message)}});controls.append(check);if(m.rollback_available){const back=textNode('button','Откатить');back.addEventListener('click',async()=>{if(!confirm('Вернуть предыдущую версию модуля?'))return;try{await api('/api/admin/modules/'+m.id+'/rollback',{method:'POST'});await load();toast('Модуль откатан')}catch(e){toast(e.message)}});controls.append(back)}row.append(controls);list.append(row);}}
function setBusy(form,busy){const b=form.querySelector('button');b.disabled=busy;b.dataset.old=b.dataset.old||b.textContent;b.textContent=busy?'Обработка…':b.dataset.old;}
async function upload(formId,inputId,url){const form=$(formId);const file=$(inputId).files[0];if(!file)return;const data=new FormData();data.append('file',file);setBusy(form,true);try{const res=await api(url,{method:'POST',body:data,upload:true});toast(res.message||'Файл принят');form.reset();await load();}catch(e){toast(e.message)}finally{setBusy(form,false)}}
$('loginForm').addEventListener('submit',async e=>{e.preventDefault();try{const a=await api('/api/auth/login',{method:'POST',body:{password:$('password').value}});csrf=a.csrf;$('password').value='';$('loginError').textContent='';togglePanel(true);await load();}catch(err){$('loginError').textContent=err.message;}});
$('logout').addEventListener('click',async()=>{try{await api('/api/auth/logout',{method:'POST'});}finally{csrf='';togglePanel(false)}});
document.querySelectorAll('.tab').forEach(tab=>tab.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===tab));document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id==='tab-'+tab.dataset.tab));}));
$('settingsForm').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget;const changes={};for(const name of ['show_clock','show_date','show_weather','clock_shift','night_mode'])changes[name]=f.elements[name].checked;for(const name of ['city','timezone','time_format','clock_position','video_mode'])changes[name]=f.elements[name].value;for(const name of ['latitude','longitude','night_opacity'])changes[name]=Number(f.elements[name].value);for(const name of ['video_interval_minutes','night_start','night_end'])changes[name]=Number(f.elements[name].value);changes.night_opacity/=100;try{await api('/api/admin/config',{method:'PUT',body:{changes}});$('settingsMessage').textContent='Сохранено';toast('Настройки применены');await load();}catch(e){toast(e.message)}});
$('settingsForm').elements.night_opacity.addEventListener('input',updateNightValue);
$('reloadDisplay').addEventListener('click',async()=>{try{await api('/api/admin/display/reload',{method:'POST'});toast('Экран перезагрузится при следующей проверке')}catch(e){toast(e.message)}});
$('videoForm').addEventListener('submit',e=>{e.preventDefault();upload('videoForm','videoInput','/api/admin/videos')});
$('moduleForm').addEventListener('submit',e=>{e.preventDefault();upload('moduleForm','moduleInput','/api/admin/modules/install')});
$('coreForm').addEventListener('submit',e=>{e.preventDefault();upload('coreForm','coreInput','/api/admin/core/install')});
init();

async function checkGitHub(){
  const state=$('githubState');state.textContent='Проверяю приватный репозиторий…';
  $('githubInstall').disabled=true;
  try{const r=await api('/api/admin/github/status');
    state.textContent=`Установлено: ${r.installed?.slice(0,12)||'неизвестно'} · GitHub main: ${r.latest.slice(0,12)} · ${r.update_available?'Есть обновление':'Актуальная версия'}`;
    $('githubInstall').disabled=!r.update_available;
  }catch(e){state.textContent=e.message;toast(e.message)}
}
$('githubCheck').addEventListener('click',checkGitHub);
$('githubInstall').addEventListener('click',async()=>{
  if(!confirm('Установить новую версию из GitHub? При ошибке запуска будет откат.'))return;
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
