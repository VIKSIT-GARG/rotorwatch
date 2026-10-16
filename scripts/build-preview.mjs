import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root=fileURLToPath(new URL('../',import.meta.url));
const web=path.join(root,'web');
const output=process.argv[2]||path.join(root,'dist','rotorwatch-dashboard.html');
let html=await readFile(path.join(web,'index.html'),'utf8');
const css=await readFile(path.join(web,'styles.css'),'utf8');
const mark='data:image/svg+xml,'+encodeURIComponent(await readFile(path.join(web,'mark.svg'),'utf8'));
const modules=await Promise.all(['core.js','demo.js','app.js'].map(p=>readFile(path.join(web,p),'utf8')));
const code=modules.map((s,i)=>(i===2?'const esc=escapeHtml;\n':'')+s.replace(/^import[^\n]*\n/gm,'').replace(/^export /gm,'')).join('\n');
html=html.replace('<link rel="stylesheet" href="./styles.css">','<style>'+css+'</style>')
 .replaceAll('./mark.svg',mark)
 .replace('<script type="module" src="./app.js"></script>','<script type="module">'+code.replace(/<\/script/gi,'<\\/script')+'</script>');
await mkdir(path.dirname(output),{recursive:true});
await writeFile(output,html);
console.log('Created '+output);
