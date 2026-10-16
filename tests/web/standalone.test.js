import assert from 'node:assert/strict';
import test from 'node:test';
import {execFileSync} from 'node:child_process';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {parseHTML} from 'linkedom';

test('the delivered standalone HTML boots without imported modules',async()=>{
 const dir=await mkdtemp(path.join(os.tmpdir(),'rotorwatch-preview-'));
 const file=path.join(dir,'dashboard.html');
 const interval=globalThis.setInterval;
 try{
  execFileSync(process.execPath,['scripts/build-preview.mjs',file]);
  const html=await readFile(file,'utf8');
  assert.ok(!html.includes('src="./app.js"'));
  const code=html.match(/<script type="module">([\s\S]*?)<\/script>/)[1];
  const {window}=parseHTML(html);const memory=new Map();
  Object.assign(globalThis,{window,document:window.document,location:{hash:'',hostname:'preview.example',origin:'https://preview.example'},sessionStorage:{getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v)}});
  globalThis.setInterval=()=>0;
  await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
  assert.match(document.querySelector('#page-content').textContent,/Your build, in stock/);
  assert.equal(document.querySelectorAll('tbody tr').length,8);
 }finally{globalThis.setInterval=interval;await rm(dir,{recursive:true,force:true});}
});
