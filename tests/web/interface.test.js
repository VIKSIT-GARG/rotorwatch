import assert from 'node:assert/strict';
import test from 'node:test';
import {readFile} from 'node:fs/promises';
import {parseHTML} from 'linkedom';

// DOM integration tests; no browser, server, network, or visual assertions.
test('workspace navigation, synthetic updates, watch toggle, chat and purchase review',async()=>{
 const {window}=parseHTML(await readFile(new URL('../../web/index.html',import.meta.url),'utf8'));
 const document=window.document;
 const memory=new Map();
 const sessionStorage={getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v),removeItem:k=>memory.delete(k)};
 const location={hash:'',hostname:'dashboard.example',origin:'https://dashboard.example'};
 Object.assign(globalThis,{window,document,sessionStorage,location});
 const realInterval=globalThis.setInterval,realTimeout=globalThis.setTimeout;
 globalThis.setInterval=()=>0;globalThis.setTimeout=()=>0;
 for(const dialog of document.querySelectorAll('dialog')){dialog.showModal=()=>dialog.open=true;dialog.close=()=>dialog.open=false;}
 const flush=()=>new Promise(resolve=>setImmediate(resolve));
 const click=selector=>document.querySelector(selector).dispatchEvent(new window.Event('click',{bubbles:true}));
 const navigate=async hash=>{location.hash='#'+hash;window.dispatchEvent(new window.Event('hashchange'));await flush();};
 try{
  await import('../../web/app.js');
  assert.match(document.querySelector('#page-content').textContent,/Your build, in stock/);
  assert.match(document.querySelector('#mode-notice').textContent,/synthetic/);
  assert.equal(document.querySelectorAll('tbody tr').length,8);
  const first=document.querySelector('[data-action="toggle-watch"]');
  assert.equal(first.getAttribute('aria-pressed'),'true');
  click('[data-action="toggle-watch"]');await flush();
  assert.equal(document.querySelectorAll('tbody tr').length,7);
  click('[data-action="scan"]');await flush();
  assert.match(document.querySelector('#toast').textContent,/synthetic/);
  await navigate('assistant');
  click('[data-action="prompt"]');await flush();
  assert.match(document.querySelector('#chat-messages').textContent,/Demo assistant/);
  assert.ok(document.querySelectorAll('.chat-product').length>0);
  await navigate('explore');
  click('[data-action="purchase"]');await flush();
  assert.match(document.querySelector('#detail-dialog').textContent,/review/i);
  const form=document.querySelector('#purchase-form');
  document.querySelector('#purchase-quantity').value='2';
  form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush();
  await navigate('purchases');
  assert.match(document.querySelector('#page-content').textContent,/× 2/);
  assert.match(document.querySelector('#page-content').textContent,/No cart was changed/);
  await navigate('alerts');assert.match(document.querySelector('#page-content').textContent,/Telegram/);
  await navigate('settings');assert.match(document.querySelector('#page-content').textContent,/Keep credentials/);
 }finally{globalThis.setInterval=realInterval;globalThis.setTimeout=realTimeout;}
});
