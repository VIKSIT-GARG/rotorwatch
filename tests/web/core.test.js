import assert from 'node:assert/strict';
import test from 'node:test';
import {createDemo} from '../../web/demo.js';
import {filterProducts,safeLink,escapeHtml,csvCell,validateBackend,priceChange} from '../../web/core.js';

test('filters combine availability, source, budget and watch state',()=>{
 const {products}=createDemo();
 const rows=filterProducts(products,{query:'Pi 5',status:'in_stock',maxPrice:'10000',watchedOnly:true});
 assert.ok(rows.length>0);
 assert.ok(rows.every(p=>p.status==='in_stock'&&p.price_minor<=1000000&&p.watched));
 assert.equal(filterProducts(products,{query:'unfindable'}).length,0);
 const unknown={...products[0],price_minor:null};
 assert.equal(filterProducts([unknown],{maxPrice:'99999'}).length,0);
});

test('retailer links and backend origins reject dangerous inputs',()=>{
 assert.equal(safeLink('javascript:alert(1)'),'#');
 assert.equal(safeLink('https://robu.in.evil.example/product/a'),'#');
 assert.equal(safeLink('https://user:pass@robu.in/product/a'),'#');
 assert.equal(safeLink('https://robu.in/product/a'),'https://robu.in/product/a');
 assert.equal(validateBackend('https://api.example/'),'https://api.example');
 assert.equal(validateBackend('http://127.0.0.1:8000'),'http://127.0.0.1:8000');
 for(const bad of ['http://api.example','https://x.example/api','https://u:p@x.example','https://x.example/?token=secret'])assert.throws(()=>validateBackend(bad));
});

test('retailer strings render as text and CSV formulas cannot execute',()=>{
 assert.equal(escapeHtml('<script>"x"</script>'),'&lt;script&gt;&quot;x&quot;&lt;/script&gt;');
 assert.equal(csvCell('=IMPORTDATA("https://evil")'),'"\'=IMPORTDATA(""https://evil"")"');
 assert.equal(priceChange({price_minor:90,previous_price_minor:100}).direction,'down');
});
