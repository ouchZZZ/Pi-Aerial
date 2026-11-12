'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'web/admin.html'),'utf8');
const admin=fs.readFileSync(path.join(root,'web/admin.js'),'utf8');
const i18n=fs.readFileSync(path.join(root,'web/i18n.js'),'utf8');
const begin=admin.indexOf("$('onlineRefreshIntervalSave').addEventListener('click',async event=>{");
const end=admin.indexOf("\n$('onlineRefresh').addEventListener('click'",begin);
assert.ok(begin>=0&&end>begin,'interval save listener exists');
const snippet=admin.slice(begin,end);

test('source settings offer editable 60-minute default',()=>{
  assert.match(html,/id="onlineRefreshIntervalMinutes"[^>]*min="1"[^>]*step="1"[^>]*value="60"/);
  assert.match(html,/id="onlineRefreshIntervalSave"/);
  assert.match(admin,/online_refresh_interval_minutes\?\?60/);
  for(const term of [
    'Проверять включённые источники каждые, мин',
    'Сохранить интервал проверки',
    'Интервал сохранён',
    'Настройки проверки источников сохранены'
  ]){
    assert.ok(i18n.includes("['"+term+"','"),'missing English translation: '+term);
  }
});

test('admin saves a new interval through authenticated config API',async()=>{
  const calls=[],toasts=[];
  const field={value:'90'},button={disabled:false},message={textContent:''};
  let callback,loads=0;
  const controls={
    onlineRefreshIntervalSave:{addEventListener(type,fn){
      assert.equal(type,'click');callback=fn;
    }},
    onlineRefreshIntervalMinutes:field,
    onlineRefreshIntervalMessage:message,
  };
  const context={
    $:id=>controls[id],
    PiI18n:{translate:text=>text},
    api:async(uri,options)=>{calls.push({uri,options});return {ok:true}},
    load:async()=>{loads++},
    toast:text=>toasts.push(text),
  };
  vm.runInNewContext(snippet,context);
  assert.equal(typeof callback,'function');
  await callback({currentTarget:button});
  assert.equal(button.disabled,false);
  assert.equal(calls.length,1);
  assert.equal(calls[0].uri,'/api/admin/config');
  assert.equal(calls[0].options.method,'PUT');
  assert.equal(calls[0].options.body.changes.online_refresh_interval_minutes,90);
  assert.equal(loads,1);
  assert.equal(message.textContent,'Интервал сохранён');

  field.value='0';
  await callback({currentTarget:button});
  field.value='2.5';
  await callback({currentTarget:button});
  field.value='';
  await callback({currentTarget:button});
  assert.equal(calls.length,1,'invalid minute values must not reach server');
  assert.ok(toasts.some(x=>x.includes('не меньше 1')));
});
