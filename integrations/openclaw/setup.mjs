import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import {homedir} from 'node:os';

const env=process.env;
if(!env.OPENCLAW_GATEWAY_TOKEN||!env.ROTORWATCH_API_TOKEN||!env.TELEGRAM_BOT_TOKEN||!env.WHATSAPP_TO||!env.TELEGRAM_ALLOWED_CHAT_IDS)throw Error('Set both API/gateway tokens, Telegram token/private IDs and WHATSAPP_TO first.');
const ids=env.TELEGRAM_ALLOWED_CHAT_IDS.split(',').map(s=>s.trim()).filter(Boolean);
if(ids.some(x=>!/^\d+$/.test(x))||!/^\+?[1-9]\d{7,14}$/.test(env.WHATSAPP_TO))throw Error('Use private numeric Telegram IDs and an international WhatsApp number.');
const file=env.OPENCLAW_CONFIG_PATH||'/home/node/.openclaw/openclaw.json';
let config={};
try{config=JSON.parse(await readFile(file,'utf8'));await writeFile(file+'.before-rotorwatch',JSON.stringify(config,null,2),{mode:0o600});}catch(e){if(e.code!=='ENOENT')throw Error('Existing OpenClaw config is not plain JSON; merge the documented configuration manually. It was not changed.');}
config.gateway={...config.gateway,mode:'local',bind:'lan',auth:{mode:'token',token:'${OPENCLAW_GATEWAY_TOKEN}'}};
config.gateway.controlUi={...config.gateway.controlUi,allowedOrigins:[...new Set([...(config.gateway.controlUi?.allowedOrigins||[]),'http://127.0.0.1:18789','http://localhost:18789'])]};
config.channels={...config.channels,
 telegram:{...config.channels?.telegram,enabled:true,botToken:'${TELEGRAM_BOT_TOKEN}',dmPolicy:'allowlist',allowFrom:ids,groupPolicy:'disabled'},
 whatsapp:{...config.channels?.whatsapp,enabled:true,dmPolicy:'allowlist',allowFrom:['+'+env.WHATSAPP_TO.replace(/^\+/, '')],groupPolicy:'disabled',selfChatMode:env.OPENCLAW_WHATSAPP_SELF_CHAT==='true'}};
config.browser={...config.browser,enabled:true,headless:true,evaluateEnabled:true,defaultProfile:'openclaw',ssrfPolicy:{...config.browser?.ssrfPolicy,dangerouslyAllowPrivateNetwork:false}};
config.tools={...config.tools,allow:[...new Set([...(config.tools?.allow||[]),'message','browser','rotorwatch_query','rotorwatch_purchase'])]};
config.plugins={...config.plugins,load:{...config.plugins?.load,paths:[...new Set([...(config.plugins?.load?.paths||[]),'/opt/rotorwatch-plugin'])]},entries:{...config.plugins?.entries,rotorwatch:{enabled:true,config:{apiUrl:env.ROTORWATCH_API_URL||'http://assistant:8000'}}}};
config.agents={...config.agents,defaults:{...config.agents?.defaults,model:config.agents?.defaults?.model||{primary:env.OPENCLAW_MODEL||'openrouter/'+(env.LLM_MODEL||'openai/gpt-4.1-nano')}}};
const entries={...config.agents.entries};
for(const {id,default:oldDefault,...rest} of config.agents.list||[])if(id)entries[id]=rest;
delete config.agents.list;
config.agents.ownership='explicit';
config.agents.defaults.systemAgent={agentId:'main'};
config.agents.defaults.heartbeat={...config.agents.defaults.heartbeat,agentId:'main',every:'0m'};
config.agents.entries={...entries,main:{...entries.main,tools:{allow:['rotorwatch_query','rotorwatch_purchase']}},'rotorwatch-bridge':{...entries['rotorwatch-bridge'],tools:{allow:['message','browser']}}};
const workspaceSetting=entries.main?.workspace||config.agents.defaults.workspace||path.join(path.dirname(file),'workspace-main');
const workspace=path.resolve(workspaceSetting.replace(/^~(?=\/|$)/,homedir()));
config.agents.entries.main.workspace=workspace;
await mkdir(workspace,{recursive:true});
const agentsFile=path.join(workspace,'AGENTS.md');
let instructions='';
try{instructions=await readFile(agentsFile,'utf8');}catch(e){if(e.code!=='ENOENT')throw e;}
const guidance=`<!-- rotorwatch-shopping:start -->
## RotorWatch product and cart requests
You are connected to the owner's real RotorWatch backend through rotorwatch_query.
For EVERY question about product prices, availability, retailers, descriptions, history, watchlists, or adding to cart, call rotorwatch_query FIRST.
Pass the COMPLETE CURRENT USER MESSAGE VERBATIM as message. Do not replace product words with the previous product. For example an Arduino question must never become a Raspberry Pi question.
The backend can refresh retailer prices and prepare a browser cart review. Do not say you lack cart access before calling it. Report actual returned blockers if any.
For a cart request use rotorwatch_query; it returns an exact C-number review with page price, variant, quantity and confirmation instructions.
Only when the user explicitly replies 'confirm cart C<number>', pass that exact message to rotorwatch_query. Never create a confirmation yourself and never infer it from a price question or a generic yes.
Relay the backend answer including product IDs, retailer, observation times, links, unknown prices and errors. Do not invent or substitute a price, product, cart success or checkout result.
If a product lookup has no match, ask for an exact retailer URL or P-number. Do not fall back to the previous product.
Follow-up store-only requests are resolved by the backend's saved shopping context. Send the words verbatim.
Checkout and payment remain manual. Never handle passwords or OTPs in chat.
<!-- rotorwatch-shopping:end -->`;
const block=/<!-- rotorwatch-shopping:start -->[\s\S]*?<!-- rotorwatch-shopping:end -->/;
const updated=block.test(instructions)?instructions.replace(block,guidance):instructions.trimEnd()+'\n\n'+guidance+'\n';
if(updated!==instructions){if(instructions)await writeFile(agentsFile+'.before-rotorwatch-shopping',instructions,{mode:0o600});await writeFile(agentsFile,updated,{mode:0o600});}
config.bindings=[...(config.bindings||[]).filter(b=>!['telegram','whatsapp'].includes(b.match?.channel)),{agentId:'main',match:{channel:'telegram',accountId:'default'}},{agentId:'main',match:{channel:'whatsapp',accountId:'default'}}];
await mkdir(path.dirname(file),{recursive:true});
await writeFile(file,JSON.stringify(config,null,2)+'\n',{mode:0o600});
console.log('RotorWatch config saved. Both channels are selected; WhatsApp QR login and channel tests are still required.');
