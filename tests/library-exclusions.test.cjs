'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'web/admin.html'),'utf8');
const js=fs.readFileSync(path.join(root,'web/admin.js'),'utf8');
const i18n=fs.readFileSync(path.join(root,'web/i18n.js'),'utf8');

test('collapsed excluded list is at bottom of library',()=>{
  assert.match(html,/<details id="excludedLibraryCard"[\s\S]*?id="excludedLibraryList"/);
  assert.ok(html.indexOf('id="excludedLibraryCard"')>
            html.indexOf('id="localLibraryCard"'));
  assert.match(html,/id="excludedLibraryCount"/);
});

test('each online clip can be excluded and restored using authenticated API',()=>{
  assert.match(js,/const exclude=textNode\('button','Исключить'\)/);
  assert.match(js,/encodeURIComponent\(v.id\)\+'\/excluded'/);
  assert.match(js,/body:\{excluded:true\}/);
  assert.match(js,/body:\{excluded:false\}/);
  assert.match(js,/renderExcludedLibrary\(\)/);
  assert.match(js,/data\.excluded/);
});

test('exclusion UI has bilingual labels and warns about deleting MP4',()=>{
  for(const key of ['Исключить','Вернуть',
                    'Исключить ролик и удалить скачанный файл?',
                    'Исключить ролик из библиотеки?',
                    'Исключённых роликов пока нет.']){
    assert.ok(i18n.includes("['"+key+"','"),'Translation missing: '+key);
  }
  assert.match(js,/v\.cached\?'Исключить ролик и удалить скачанный файл\?'/);
});
