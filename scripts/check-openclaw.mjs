import {mkdtemp,mkdir,cp,writeFile,readFile,symlink,rm} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';

const runtime=path.resolve(process.argv[2]||'node_modules/openclaw');
const root=fileURLToPath(new URL('../',import.meta.url));
const work=await mkdtemp(path.join(os.tmpdir(),'rotorwatch-openclaw-check-'));
try{
 await mkdir(path.join(work,'node_modules'));
 await symlink(runtime,path.join(work,'node_modules/openclaw'),'dir');
 const plugin=path.join(work,'rotorwatch-plugin');
 await cp(path.join(root,'integrations/openclaw'),plugin,{recursive:true});
 const state=path.join(work,'state'),config=path.join(state,'openclaw.json');
 const env={...process.env,OPENCLAW_STATE_DIR:state,OPENCLAW_CONFIG_PATH:config,
  OPENCLAW_GATEWAY_TOKEN:'temporary-test-gateway-token',ROTORWATCH_API_TOKEN:'temporary-test-workspace-token',
  TELEGRAM_BOT_TOKEN:'test-only-not-a-bot-token',TELEGRAM_ALLOWED_CHAT_IDS:'42',WHATSAPP_TO:'919900000000'};
 const run=args=>execFileSync(process.execPath,args,{env,encoding:'utf8',timeout:90000});
 run([path.join(plugin,'setup.mjs')]);
 const data=JSON.parse(await readFile(config,'utf8'));data.plugins.load.paths=[plugin];
 await writeFile(config,JSON.stringify(data));
 const cli=path.join(runtime,'openclaw.mjs');
 const validation=JSON.parse(run([cli,'config','validate','--json']));
 assert.equal(validation.valid,true);assert.deepEqual(validation.warnings||[],[]);
 const inspection=JSON.parse(run([cli,'plugins','inspect','rotorwatch','--runtime','--json']));
 assert.equal(inspection.plugin.status,'loaded');
 assert.deepEqual(inspection.plugin.toolNames.sort(),['rotorwatch_purchase','rotorwatch_query']);
 assert.ok(!(inspection.diagnostics||[]).some(d=>d.level==='error'));
 if((inspection.diagnostics||[]).length)console.log('Local plugin provenance warnings are expected for this source bundle.');
 console.log('OpenClaw config validated; plugin loaded with both tools. No gateway, channel connection or messages were started.');
}finally{await rm(work,{recursive:true,force:true});}
