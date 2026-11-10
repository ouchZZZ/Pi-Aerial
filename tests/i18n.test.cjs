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
