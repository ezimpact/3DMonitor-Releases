import {test} from 'node:test';
import assert from 'node:assert/strict';
import {describePrinterError} from '../k1monitor/static/printer-errors.js';
import {normalizeAppearance,contrastColor,validateBackgroundFile} from '../k1monitor/static/appearance.js';
const item=(code,options={})=>({printer:{protocol:'creality',model:'K1 Max'},error:{code,key:String(code),message:'',active:true},...options});

test('confirmed codes decode and raw diagnostic is retained; numeric examples are not guessed',()=>{
  assert.equal(describePrinterError(item(2839)).summary,'Закончился филамент');
  assert.match(describePrinterError(item(2564)).summary,/Сопло не нагревается/);
  assert.match(describePrinterError(item(2565)).summary,/Стол не нагревается/);
  const unknown=describePrinterError(item(1295692));assert.match(unknown.summary,/Неизвестная.*1295692/);assert.match(unknown.diagnostic,/1295692/);
  assert.equal(describePrinterError(item(99,{error:{code:99,key:'key2564',message:'raw',active:true}})).summary,'Сопло не нагревается как ожидается');
});
test('protocol and model namespaces cannot borrow unrelated numeric meanings',()=>{
  for(const protocol of ['moonraker','octoprint','prusalink'])assert.match(describePrinterError(item(2564,{printer:{protocol}})).summary,/Неизвестная/);
  assert.match(describePrinterError(item(2564,{printer:{protocol:'creality',model:'K2 Plus'}})).summary,/Неизвестная/);
});
test('Klipper messages translate without changing diagnostics and simultaneous runout cannot hide a heater error',()=>{
  const raw='Heater extruder not heating at expected rate';
  const result=describePrinterError(item(-1,{printer:{protocol:'moonraker'},error:{code:-1,message:raw,active:true},filament_runout:true}));
  assert.equal(result.summary,'Сопло не нагревается как ожидается');assert.ok(result.diagnostic.includes(raw));
  assert.match(describePrinterError(item(-1,{error:{code:-1,message:"Lost communication with MCU 'mcu'"}})).summary,/Нет связи/);
  assert.equal(describePrinterError(item(2564,{filament_runout:true})).summary,'Сопло не нагревается как ожидается');
});
test('custom colors reject CSS/URL injection and clamp opacity; text contrast covers light and dark',()=>{
  const settings=normalizeAppearance({accent:'url(https://bad)',background:'#ffffff',panel:'red;display:none',shade:1000,hasImage:'true'});
  assert.equal(settings.accent,'#0872e8');assert.equal(settings.panel,'#1a222a');assert.equal(settings.shade,95);assert.equal(settings.hasImage,false);
  assert.equal(contrastColor('#ffffff'),'#121820');assert.equal(contrastColor('#000000'),'#f4f7fb');
});
test('wallpaper accepts raster images only and enforces the size limit',()=>{
  assert.doesNotThrow(()=>validateBackgroundFile({type:'image/png',size:1024}));
  assert.throws(()=>validateBackgroundFile({type:'image/svg+xml',size:1024}),/JPG/);
  assert.throws(()=>validateBackgroundFile({type:'image/jpeg',size:10*1024*1024+1}),/10 МБ/);
});

test('legacy default palettes migrate to graphite while preserving personal image and custom colors',()=>{
  const old={version:1,accent:'#a0efc1',background:'#111715',panel:'#1a221f',sidebar:'#141d18',hasImage:true,shade:37};
  const next=normalizeAppearance(old);assert.equal(next.accent,'#0872e8');assert.equal(next.hasImage,true);assert.equal(next.shade,37);
  const custom=normalizeAppearance({...old,accent:'#f1a929'});assert.equal(custom.accent,'#f1a929');assert.equal(custom.background,'#111715');
  assert.doesNotThrow(()=>normalizeAppearance({...old,accent:123}));
});
