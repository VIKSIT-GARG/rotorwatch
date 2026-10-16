import {definePluginEntry} from 'openclaw/plugin-sdk/plugin-entry';
import {registerRotorWatchTools} from './tools.js';

export default definePluginEntry({
 id:'rotorwatch',name:'RotorWatch',description:'Drone parts database and purchase browser reviews',
 register:registerRotorWatchTools,
});
