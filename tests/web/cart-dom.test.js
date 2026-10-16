import assert from 'node:assert/strict';
import test from 'node:test';
import {execFileSync} from 'node:child_process';
import {parseHTML} from 'linkedom';

test('fixed cart scripts inspect a scoped product form and click only Add to cart once',()=>{
 const scripts=JSON.parse(execFileSync('python3',['tests/web/cart-script-fixture.py'],{encoding:'utf8'}));
 const {window}=parseHTML('<html><body><div class="summary"><h1>Pi 5</h1><span class="price">₹6,000</span><form class="cart"><input name="quantity" type="number" value="1"><button type="button">Add to cart</button><button type="button">Buy now</button></form></div></body></html>');
 Object.assign(globalThis,{document:window.document,location:new URL('https://robu.in/product/pi5/'),HTMLInputElement:window.HTMLInputElement,Event:window.Event});
 let cart=0,buy=0;const buttons=document.querySelectorAll('button');buttons[0].addEventListener('click',()=>cart++);buttons[1].addEventListener('click',()=>buy++);
 const probe=Function('return ('+scripts.inspect+')()')();
 assert.equal(probe.title,'Pi 5');assert.equal(probe.blocked,'');
 assert.equal(Function('return ('+scripts.submit+')()')().clicked,true);
 assert.equal(document.querySelector('input').value,'2');assert.equal(cart,1);assert.equal(buy,0);
 location=new URL('https://robu.in/product/another-product/');
 assert.equal(Function('return ('+scripts.submit+')()')().clicked,false);assert.equal(cart,1);
});

test('ambiguous add controls and unselected variants stop before mutation',()=>{
 const {inspect}=JSON.parse(execFileSync('python3',['tests/web/cart-script-fixture.py'],{encoding:'utf8'}));
 for(const markup of ['<form class="cart"><button>Add to cart</button><button>Add to cart</button></form>','<form class="cart"><select name="attribute_ram"><option value="">Choose RAM</option></select><input name="variation_id" value="0"><button>Add to cart</button></form>']){
  const {window}=parseHTML(markup);Object.assign(globalThis,{document:window.document,location:new URL('https://robu.in/product/pi5/')});
  assert.ok(Function('return ('+inspect+')()')().blocked);
 }
});
