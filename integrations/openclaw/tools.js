export function registerRotorWatchTools(api,{fetchImpl=globalThis.fetch,env=process.env}={}){
 const base=api.pluginConfig?.apiUrl||env.ROTORWATCH_API_URL||'http://assistant:8000';
 const origin=new URL(base);
 const local=['assistant','127.0.0.1','localhost','[::1]'].includes(origin.hostname);
 if(origin.username||origin.password||origin.search||origin.hash||origin.pathname!=='/'||(origin.protocol!=='https:'&&!(origin.protocol==='http:'&&local)))throw Error('RotorWatch requires a private local API origin or HTTPS.');
 const token=env.ROTORWATCH_API_TOKEN;
 const call=async(path,body)=>{
  if(!token)throw Error('Configure ROTORWATCH_API_TOKEN in the OpenClaw gateway environment.');
  let response;
  try{response=await fetchImpl(origin.origin+path,{method:'POST',redirect:'error',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(180000)});}catch{throw Error('RotorWatch backend is unreachable. Check the private API connection.');}
  const result=await response.json().catch(()=>({}));
  if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'RotorWatch request failed ('+response.status+').');
  return result;
 };
 api.registerTool({
  name:'rotorwatch_query',label:'Query drone parts',
  description:'REQUIRED for every product stock, latest price, cart, comparison, history or watchlist request. Pass the owner\'s complete current message VERBATIM, including the exact product/store and any confirm cart C-number. The backend refreshes price requests and handles cart previews and explicit chat confirmations. Relay its answer faithfully. Never reuse a previous product or invent prices or cart results.',
  parameters:{type:'object',properties:{message:{type:'string',minLength:1,maxLength:1200}},required:['message'],additionalProperties:false},
  async execute(_id,{message}){
   const result=await call('/api/chat',{message,session_id:'openclaw'});
   return{content:[{type:'text',text:result.answer}],details:{products:result.products,model_used:result.model_used}};
  },
 });
 api.registerTool({
  name:'rotorwatch_purchase',label:'Prepare a drone parts cart review',
  description:'Optional dashboard review helper: action review saves product_id and quantity; action prepare inspects an existing review_id. Prefer rotorwatch_query with the full verbatim message for chat add-to-cart requests and confirmations. Do not claim inability to use a cart without checking the tool. No checkout or payment.',
  parameters:{type:'object',properties:{action:{type:'string',enum:['review','prepare']},product_id:{type:'integer',minimum:1},quantity:{type:'integer',minimum:1,maximum:20},review_id:{type:'integer',minimum:1}},required:['action'],additionalProperties:false},
  async execute(_id,p){
   let result;
   if(p.action==='review'){
    if(!Number.isInteger(p.product_id)||p.product_id<1)throw Error('Choose a product ID from the database first.');
    result=await call('/api/purchases',{product_id:p.product_id,quantity:p.quantity||1});
   }else{
    if(!Number.isInteger(p.review_id)||p.review_id<1)throw Error('Create a purchase review first.');
    result=await call('/api/purchases/'+p.review_id+'/prepare-browser',{});
   }
   return{content:[{type:'text',text:JSON.stringify(result)}],details:result};
  },
 });
}
