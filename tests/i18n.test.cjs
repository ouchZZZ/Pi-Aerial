'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const root=path.join(__dirname,'..');
const script=fs.readFileSync(path.join(root,'web/i18n.js'),'utf8');
const mapScript=fs.readFileSync(path.join(root,'web/admin-location.js'),'utf8');

function makeLocalization(){
  const browser={};
  const document={documentElement:{lang:'ru'},body:null,title:'Pi Aerial · Управление'};
  vm.runInNewContext(script,{window:browser,document},{filename:'i18n.js'});
  return {browser,document,i18n:browser.PiI18n};
}
test('English dictionary translates administrative labels, messages and combinations',()=>{
  const {i18n,document}=makeLocalization();
  assert.equal(i18n.translate('Вход в панель'),'Вход в панель');
  i18n.setLocale('en');
  assert.equal(document.documentElement.lang,'en');
  assert.equal(i18n.translate('Вход в панель'),'Sign in');
  assert.equal(i18n.translate('Видеозаписей'),'Videos');
  assert.equal(i18n.translate('Сейчас загрузка и обработка не выполняются.'),
    'No active downloads or conversions.');
  assert.equal(i18n.translate('Использовано 1.3 из 12 ГБ'),'Used 1.3 of 12 GB');
  assert.equal(i18n.translate('Город выбран. Сохраните настройки.'),
    'City selected. Save settings.');
  i18n.setLocale('ru');
  assert.equal(i18n.translate('Вход в панель'),'Вход в панель');
  assert.equal(document.documentElement.lang,'ru');
});
test('location picker module exports city search, timezone listing and map handlers',()=>{
  const browser={};
  vm.runInNewContext(mapScript,{window:browser},{filename:'admin-location.js'});
  assert.equal(typeof browser.PiLocationPicker.init,'function');
  assert.equal(typeof browser.PiLocationPicker.sync,'function');
  assert.equal(typeof browser.PiLocationPicker.loadTimezones,'function');
  assert.equal(typeof browser.PiLocationPicker.findCity,'function');
});


test('geocoder city choice fills name, coordinates and IANA zone', async()=>{
  const elements=new Map();
  function item(id){
    if(!elements.has(id)){
      const e={id,textContent:'',value:'',options:[],children:[],listeners:{},
        addEventListener(type,listener){this.listeners[type]=listener;},
        append(child){this.children.push(child);},
        replaceChildren(...children){this.children=[...children];},
        add(option){this.options.push(option);},
      };
      elements.set(id,e);
    }
    return elements.get(id);
  }
  const form=item('settingsForm');
  form.elements={city:item('city'),latitude:item('latitude'),
    longitude:item('longitude'),timezone:item('timezone')};
  form.elements.timezone.value='Europe/Moscow';
  form.elements.timezone.options.push({value:'Europe/Moscow'});
  form.elements.city.value='London';
  let fetched='';
  const browser={PiI18n:{language:'en'}};
  const document={getElementById:item,
    createElement:()=>({type:'',textContent:'',listeners:{},addEventListener(type,fn){this.listeners[type]=fn;}})};
  const fetch=async url=>{
    fetched=String(url);
    return {ok:true,json:async()=>({results:[{
      name:'London',latitude:51.5072,longitude:-0.1276,
      timezone:'Europe/London',country:'United Kingdom',
    }]})};
  };
  vm.runInNewContext(mapScript,{
    window:browser,document,fetch,URLSearchParams,AbortController,
    Option:class{constructor(label,value){this.label=label;this.value=value;}},
    console:{warn(){}},
  },{filename:'admin-location.js'});
  browser.PiLocationPicker.init(async()=>({timezones:['Europe/London','Europe/Moscow']}));
  await browser.PiLocationPicker.findCity();
  assert.match(fetched,/language=en/);
  const results=item('citySearchResults').children;
  assert.equal(results.length,1);
  results[0].listeners.click();
  assert.equal(form.elements.city.value,'London');
  assert.equal(form.elements.latitude.value,'51.50720');
  assert.equal(form.elements.longitude.value,'-0.12760');
  assert.equal(form.elements.timezone.value,'Europe/London');
  assert.equal(item('locationStatus').textContent,'Город выбран. Сохраните настройки.');
});
