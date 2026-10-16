// Called only after diagnostics identify a Chromium sandbox launch failure.
import {existsSync} from 'node:fs';
import {readFile,writeFile} from 'node:fs/promises';
if(!existsSync('/.dockerenv'))throw Error('This repair is restricted to the existing Docker gateway.');
const file=process.env.OPENCLAW_CONFIG_PATH||'/home/node/.openclaw/openclaw.json';
const original=await readFile(file,'utf8');
await writeFile(file+'.before-rotorwatch-container-browser',original,{mode:0o600});
const config=JSON.parse(original);
const browser=config.browser||{},profile=browser.profiles?.openclaw||{};
if(profile.cdpUrl||profile.attachOnly||browser.attachOnly||(profile.driver&&profile.driver!=='openclaw'))throw Error('Externally managed browser profile was left unchanged.');
config.browser={...browser,enabled:true,headless:true,noSandbox:true};
await writeFile(file,JSON.stringify(config,null,2)+'\n',{mode:0o600});
console.log('Observed container sandbox launch failure: managed Chromium noSandbox enabled. Gateway authentication and account sessions were preserved.');
