/* Search locations using Open-Meteo/GeoNames; map and reverse lookup on demand. */
(function(global){
  'use strict';
  const $=id=>document.getElementById(id);
  const f=()=>document.getElementById('settingsForm');
  let map=null,marker=null,leafletPromise=null,searchController=null,sequence=0;
  let api=null,reverseAt=0;

  function message(text){$('locationStatus').textContent=text;}
  function setValue(name,value){f().elements[name].value=String(value);}
  function coordinates(){return {lat:Number(f().elements.latitude.value),lon:Number(f().elements.longitude.value)};}
  function keepZone(zone){
    if(!zone)return;
    const select=f().elements.timezone;
    if(!Array.from(select.options).some(x=>x.value===zone)){
      const option=new Option(zone,zone);
      select.add(option);
    }
    select.value=zone;
  }
  function updateMap(lat,lon,zoom=10){
    if(!map)return;
    if(!Number.isFinite(lat)||!Number.isFinite(lon))return;
    map.setView([lat,lon],zoom);
    if(marker)marker.setLatLng([lat,lon]);
    else marker=global.L.marker([lat,lon]).addTo(map);
  }
  function setCity(result){
    const lat=Number(result.latitude),lon=Number(result.longitude);
    if(!Number.isFinite(lat)||!Number.isFinite(lon))return;
    setValue('latitude',lat.toFixed(5));
    setValue('longitude',lon.toFixed(5));
    if(result.name)setValue('city',result.name);
    if(result.timezone)keepZone(result.timezone);
    updateMap(lat,lon);
    $('citySearchResults').replaceChildren();
    message('Город выбран. Сохраните настройки.');
  }

  async function loadTimezones(){
    const select=f().elements.timezone;
    const original=select.value;
    try{
      const result=await api('/api/admin/timezones');
      if(!Array.isArray(result.timezones)||!result.timezones.length)throw Error('empty zones');
      const groups=new Map();
      for(const zone of result.timezones){
        if(typeof zone!=='string'||!zone||zone.startsWith('Etc/')||zone.startsWith('posix/')||zone.startsWith('right/'))continue;
        const region=zone.includes('/')?zone.split('/')[0]:'Other';
        if(!groups.has(region))groups.set(region,[]);
        groups.get(region).push(zone);
      }
      const fragment=document.createDocumentFragment();
      for(const region of [...groups.keys()].sort()){
        const group=document.createElement('optgroup');
        group.label=region;
        for(const zone of groups.get(region)){
          const option=document.createElement('option');
          option.value=zone;option.textContent=zone.replaceAll('_',' ');
          group.append(option);
        }
        fragment.append(group);
      }
      select.replaceChildren(fragment);
      keepZone(original||'Europe/Moscow');
    }catch(e){
      console.warn('IANA zones unavailable:',e);
      message('Не удалось загрузить список часовых поясов.');
    }
  }

  async function findCity(){
    const name=f().elements.city.value.trim();
    const results=$('citySearchResults');
    results.replaceChildren();
    if(name.length<2){message('Введите минимум 2 символа названия города.');return;}
    if(searchController)searchController.abort();
    searchController=new AbortController();
    const local=searchController;
    message('Ищу города…');
    try{
      const params=new URLSearchParams({name,count:'10',language:global.PiI18n?.language||'ru',format:'json'});
      const response=await fetch('https://geocoding-api.open-meteo.com/v1/search?'+params,{signal:local.signal});
      if(!response.ok)throw Error('Location search HTTP '+response.status);
      const data=await response.json();
      if(local.signal.aborted)return;
      const places=Array.isArray(data.results)?data.results:[];
      if(!places.length){message('Города не найдены. Уточните запрос.');return;}
      message('Выберите город из результатов или точку на карте.');
      for(const place of places){
        if(!Number.isFinite(Number(place.latitude))||!Number.isFinite(Number(place.longitude)))continue;
        const button=document.createElement('button');
        button.type='button';
        button.textContent=[place.name,place.admin1,place.country].filter(Boolean).join(', ');
        button.addEventListener('click',()=>{++sequence;setCity(place);});
        results.append(button);
      }
    }catch(error){
      if(error.name!=='AbortError'){message('Не удалось получить список: '+error.message);}
    }
  }

  function leaflet(){
    if(global.L)return Promise.resolve(global.L);
    if(leafletPromise)return leafletPromise;
    leafletPromise=new Promise((resolve,reject)=>{
      const stylesheet=document.createElement('link');
      stylesheet.rel='stylesheet';
      stylesheet.href='https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css';
      stylesheet.integrity='sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=';
      stylesheet.crossOrigin='anonymous';
      document.head.append(stylesheet);
      const script=document.createElement('script');
      script.src='https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js';
      script.integrity='sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=';
      script.crossOrigin='anonymous';
      script.onload=()=>global.L?resolve(global.L):reject(Error('Leaflet unavailable'));
      script.onerror=()=>reject(Error('Leaflet CDN unavailable'));
      document.head.append(script);
    }).catch(e=>{leafletPromise=null;throw e;});
    return leafletPromise;
  }

  async function toggleMap(){
    const target=$('weatherMap');
    if(!target.hidden){
      target.hidden=true;
      $('cityMapToggle').textContent='Показать карту';
      return;
    }
    target.hidden=false;
    $('cityMapToggle').textContent='Скрыть карту';
    try{
      const L=await leaflet();
      if(target.hidden)return;
      if(!map){
        const c=coordinates();
        const lat=Number.isFinite(c.lat)?c.lat:55.7558;
        const lon=Number.isFinite(c.lon)?c.lon:37.6173;
        map=L.map(target,{worldCopyJump:true}).setView([lat,lon],9);
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
          attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
          maxZoom:18,noWrap:false
        }).addTo(map);
        marker=L.marker([lat,lon]).addTo(map);
        map.on('click',event=>selectPoint(event.latlng.lat,event.latlng.lng));
      }else{
        const c=coordinates();
        updateMap(c.lat,c.lon);
      }
      map.invalidateSize();
      message('Нажмите на карту, чтобы выбрать координаты и ближайший город.');
    }catch(error){
      console.warn('Map unavailable:',error);
      target.hidden=true;
      $('cityMapToggle').textContent='Показать карту';
      message('Не удалось загрузить карту. Введите город и координаты вручную.');
    }
  }

  async function selectPoint(lat,lon){
    const seq=++sequence;
    const safeLat=Math.max(-90,Math.min(90,Number(lat)));
    const safeLon=Math.max(-180,Math.min(180,Number(lon)));
    setValue('latitude',safeLat.toFixed(5));
    setValue('longitude',safeLon.toFixed(5));
    setValue('city',safeLat.toFixed(3)+', '+safeLon.toFixed(3));
    updateMap(safeLat,safeLon,map?.getZoom()||10);
    $('citySearchResults').replaceChildren();
    message('Определяю ближайший населённый пункт…');
    const lang=global.PiI18n?.language||'ru';

    // One deliberate request per map click. Respect Nominatim's 1 request/s policy.
    const now=Date.now();
    const wait=Math.max(0,1100-(now-reverseAt));
    await new Promise(resolve=>setTimeout(resolve,wait));
    if(seq!==sequence)return;
    reverseAt=Date.now();
    try{
      const p=new URLSearchParams({format:'jsonv2',lat:String(safeLat),lon:String(safeLon),
        zoom:'10',addressdetails:'1','accept-language':lang});
      const response=await fetch('https://nominatim.openstreetmap.org/reverse?'+p,{
        referrerPolicy:'strict-origin-when-cross-origin'
      });
      if(!response.ok)throw Error('Reverse location HTTP '+response.status);
      const data=await response.json();
      if(seq!==sequence)return;
      const address=data.address||{};
      const city=address.city||address.town||address.village||address.municipality||
        address.hamlet||address.county||address.state;
      if(city)setValue('city',city);
      else message('Не удалось определить город. Введите название вручную.');
    }catch(e){
      if(seq!==sequence)return;
      console.warn('Reverse city lookup failed',e);
      message('Не удалось определить город. Введите название вручную.');
    }
    // Timezone is determined at the clicked coordinates, not at an assumed city center.
    try{
      const params=new URLSearchParams({latitude:String(safeLat),longitude:String(safeLon),
        current:'temperature_2m',timezone:'auto',forecast_days:'1'});
      const response=await fetch('https://api.open-meteo.com/v1/forecast?'+params);
      if(response.ok){
        const result=await response.json();
        if(seq===sequence&&result.timezone)keepZone(result.timezone);
      }
    }catch(e){console.warn('Time zone lookup failed',e);}
    if(seq===sequence&&f().elements.city.value!==safeLat.toFixed(3)+', '+safeLon.toFixed(3))
      message('Город выбран. Сохраните настройки.');
  }

  function init(apiFunction){
    api=apiFunction;
    $('citySearch').addEventListener('click',findCity);
    f().elements.city.addEventListener('keydown',e=>{
      if(e.key==='Enter'){e.preventDefault();findCity();}
    });
    $('cityMapToggle').addEventListener('click',toggleMap);
    for(const name of ['latitude','longitude']){
      f().elements[name].addEventListener('change',()=>{
        const c=coordinates();updateMap(c.lat,c.lon,map?.getZoom()||10);
      });
    }
  }
  function sync(config){
    if(!config)return;
    keepZone(config.timezone||'Europe/Moscow');
    const lat=Number(config.latitude),lon=Number(config.longitude);
    if(map&&Number.isFinite(lat)&&Number.isFinite(lon))updateMap(lat,lon,map.getZoom());
  }
  global.PiLocationPicker={init,sync,loadTimezones,findCity};
})(window);
