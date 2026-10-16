import assert from 'node:assert/strict';
import test from 'node:test';
import {registerRotorWatchTools} from '../../integrations/openclaw/tools.js';

test('OpenClaw inbound tool queries backend with secret header and returns database answer',async()=>{
 const tools=[],requests=[];
 registerRotorWatchTools({pluginConfig:{apiUrl:'http://127.0.0.1:8000'},registerTool:t=>tools.push(t)},{env:{ROTORWATCH_API_TOKEN:'private'},fetchImpl:async(url,options)=>{requests.push({url,options});return{ok:true,json:async()=>({answer:'P1 was recorded in stock.',products:[{id:1}],model_used:true})};}});
 assert.deepEqual(tools.map(t=>t.name),['rotorwatch_query','rotorwatch_purchase']);
 const result=await tools[0].execute('call-id',{message:'Find Pi 5'});
 assert.equal(requests[0].url,'http://127.0.0.1:8000/api/chat');
 assert.equal(requests[0].options.headers.Authorization,'Bearer private');
 assert.match(result.content[0].text,/P1/);assert.deepEqual(result.details.products,[{id:1}]);
 assert.ok(!JSON.stringify(result).includes('private'));
 assert.ok(!tools[1].parameters.properties.action.enum.includes('confirm_cart'));
});

test('OpenClaw cart tool saves a review and prepares its exact browser request',async()=>{
 const tools=[],requests=[];
 registerRotorWatchTools({pluginConfig:{apiUrl:'http://assistant:8000'},registerTool:t=>tools.push(t)},{env:{ROTORWATCH_API_TOKEN:'private'},fetchImpl:async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return{ok:true,json:async()=>({id:7,state:'cart_ready'})};}});
 await tools[1].execute('id',{action:'review',product_id:1,quantity:2});
 await tools[1].execute('id',{action:'prepare',review_id:7});
 assert.deepEqual(requests[0].body,{product_id:1,quantity:2});
 assert.equal(requests[1].url,'http://assistant:8000/api/purchases/7/prepare-browser');
 await assert.rejects(tools[1].execute('id',{action:'prepare'}),/Create a purchase review/);
});
