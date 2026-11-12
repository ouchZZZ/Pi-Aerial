'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const html=fs.readFileSync(path.join(__dirname,'../web/admin.html'),'utf8');
const script=fs.readFileSync(path.join(__dirname,'../web/admin.js'),'utf8');

test('auto-update controls live only in Updates, in an independent form',()=>{
  const settings=html.match(/<form id="settingsForm">([\s\S]*?)<\/form>/)?.[1];
  const updates=html.match(/<section class="page" id="tab-updates">([\s\S]*?)<\/section>/)?.[1];
  assert.ok(settings);
  assert.ok(updates);
  assert.doesNotMatch(settings,/auto_update_enabled|auto_update_interval_minutes/);
  assert.match(updates,/<form id="autoUpdateForm" class="card">/);
  assert.match(updates,/name="auto_update_enabled"/);
  assert.match(updates,/name="auto_update_interval_minutes" min="1" step="1" value="60" required/);
  assert.match(updates,/id="autoUpdateMessage"/);
  assert.equal((html.match(/name="auto_update_enabled"/g)||[]).length,1);
  assert.equal((html.match(/name="auto_update_interval_minutes"/g)||[]).length,1);
});

test('auto-update settings load from config and save without submitting display settings',()=>{
  assert.match(script,/const field=f\.elements\[key\]\|\|a\.elements\[key\]/);
  const displaySubmit=script.match(/\$\('settingsForm'\)\.addEventListener\('submit',([\s\S]*?)\n\$\('autoUpdateForm'\)/)?.[1];
  assert.ok(displaySubmit);
  assert.doesNotMatch(displaySubmit,/auto_update_enabled|auto_update_interval_minutes/);
  const autoSubmit=script.match(/\$\('autoUpdateForm'\)\.addEventListener\('submit',([\s\S]*?)\n\$\('settingsForm'\)\.elements\.night_opacity/)?.[1];
  assert.ok(autoSubmit);
  assert.match(autoSubmit,/Number\.isInteger\(interval\)/);
  assert.match(autoSubmit,/auto_update_enabled:f\.elements\.auto_update_enabled\.checked/);
  assert.match(autoSubmit,/auto_update_interval_minutes:interval/);
  assert.match(autoSubmit,/api\('\/api\/admin\/config'/);
});
