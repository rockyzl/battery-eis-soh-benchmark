const endpoint='https://api.sciencesloop.com/api/battery-agent';
const lang=new URLSearchParams(location.search).get('lang');
const zh=lang?lang==='zh':location.pathname.startsWith('/zh/');
const t=(a,b)=>zh?b:a;
const labels={inspect_data:t('Inspect measurement records','检查测量记录'),fit_eis:t('Fit and compare EIS circuits','拟合并比较 EIS 电路'),predict_capacity:t('Evaluate capacity prediction','评估容量预测'),simulate_pulse:t('Simulate a pulse / rest response','模拟脉冲与静置响应'),finish:t('Conclude this analysis','汇总本轮分析')};
const examples=[
 {title:t('Simulate pulse voltage','模拟脉冲电压响应'),task:t('Use a one-RC Thevenin model to simulate a 0.01 A discharge pulse for 10 seconds followed by 10 seconds rest. OCV 4 V, R0 0.35 ohm, R1 0.6 ohm, tau 10 seconds. These are illustrative parameters, not identified cell parameters. Explain which voltage changes arise from each model term.','用一阶 Thevenin 等效电路模拟 0.01 A 放电脉冲 10 秒，再静置 10 秒。设 OCV 为 4 V、R0 为 0.35 Ω、R1 为 0.6 Ω、时间常数为 10 秒。这是说明性参数，不是已辨识的电池参数。解释模型中各部分对电压响应的影响。')},
 {title:t('Compare EIS stages','比较不同阶段的 EIS'),task:t('Compare measured EIS for cell 25C05 at diagnostic indices 1 and 138. Check consistency, compare RC and CPE fit errors separately, and explain the limits of the interpretation. No prediction.','比较电池 25C05 诊断序号 1 和 138 的实测 EIS。先检查数据，再分别比较 KK 残差及 RC、CPE 电路拟合误差。解释能得到什么结论和还缺少什么证据，不做预测。')},
 {title:t('Review one EIS spectrum','分析单条 EIS 谱'),task:t('Analyze measured EIS for cell 25C05 at diagnostic index 1. Compare RC and CPE fits and explain what the fit does and does not support. No prediction.','分析电池 25C05 诊断序号 1 的实测 EIS，比较 RC 和 CPE 拟合，说明拟合能支持哪些结论，并提出下一步检查，不做预测。')},
 {title:t('Test a prediction against baselines','检验预测是否优于基线'),task:t('Evaluate 25 C five-index-ahead capacity prediction from EIS. Hold out entire cells, compare against persistence and the training mean, and report failures honestly.','评估 25°C 条件下用 EIS 预测五个诊断序号后的容量，按整颗电池划分训练与测试，比较沿用当前容量和训练均值基线；如果预测更差，请明确说明。')}
];
const panel=document.createElement('section');panel.id='research-agent';panel.className='panel agent-chat-panel';
panel.innerHTML=`<div class="chat-heading"><div><div class="eyebrow">BATTERY RESEARCH AGENT</div><h2>${t('Battery analysis conversation','电池数据分析对话')}</h2></div><span class="chat-session-label">${t('Live tools · measured data','真实工具 · 实测数据')}</span></div>
<div class="chat-shell"><div class="chat-toolbar"><span id="agent-connection">${t('Connecting…','正在连接…')}</span><div><button class="chat-text-button" id="agent-catalog-toggle">${t('Analysis scenarios','分析场景与工具')}</button><button class="chat-text-button" id="agent-export">${t('Export','导出记录')}</button><button class="chat-text-button" id="agent-reset">${t('New conversation','新对话')}</button></div></div>
<div id="agent-catalog" hidden></div><div id="agent-messages" class="chat-messages" role="log" aria-label="${t('Analysis conversation','分析对话记录')}"></div>
<div class="chat-composer"><div id="agent-status" role="status" aria-live="polite"></div><form id="agent-form"><label class="sr-only" for="agent-task">${t('Research task or feedback','研究任务或反馈')}</label><textarea id="agent-task" rows="2" maxlength="2200" placeholder="${t('Ask a battery analysis question, or change the next action…','输入分析任务，或告诉 Agent 下一步要怎么调整…')}"></textarea><div class="chat-compose-bottom"><label class="chat-auto"><input type="checkbox" id="agent-auto">${t('Run recommended tools automatically in this round','本轮自动执行推荐工具')}</label><div><button type="button" class="quiet" id="agent-stop" hidden>${t('Stop','停止')}</button><button type="submit" class="primary" id="agent-run">${t('Send','发送')} ↑</button></div></div></form><p class="chat-privacy">${t('Task text and public-data results are sent to the model. Uploaded files are not connected to this conversation.','任务文字与公开数据的计算结果会发给模型。上传文件暂未接入本对话。')}</p></div></div>`;
document.querySelector('.content').prepend(panel);document.getElementById('agent-boot')?.remove();
const $=id=>document.getElementById(id),feed=$('agent-messages');
const nav=document.createElement('button');nav.className='tab active';nav.dataset.tab='research-agent';nav.textContent=t('Analysis conversation','分析对话');document.querySelector('aside').prepend(nav);
function activate(){document.querySelectorAll('.panel').forEach(p=>p.hidden=p!==panel);document.querySelectorAll('.tab').forEach(b=>{b.classList.toggle('active',b===nav);b.setAttribute('aria-pressed',String(b===nav));});document.body.classList.add('agent-chat-active');}
nav.onclick=activate;document.querySelectorAll('.tab').forEach(b=>{if(b!==nav)b.addEventListener('click',()=>document.body.classList.remove('agent-chat-active'));});activate();
let records=[],current=null,data=null,pending=null,busy=false,worker=null,requestAbort=null,runEpoch=0,statusTimer=null,sticky=true;
try{const saved=JSON.parse(sessionStorage.getItem('sciencesloop-battery-chat-v2')||'[]');if(Array.isArray(saved))records=saved.slice(-6);}catch{}
function persist(){try{sessionStorage.setItem('sciencesloop-battery-chat-v2',JSON.stringify(records.slice(-6)));}catch{setStatus(t('Session storage unavailable. Export the conversation to keep it.','浏览器无法保存会话，可导出记录。'));}}
function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function bottom(force=false){if(force||sticky)requestAnimationFrame(()=>{feed.scrollTop=feed.scrollHeight;});}
feed.addEventListener('scroll',()=>{sticky=feed.scrollHeight-feed.scrollTop-feed.clientHeight<100;});
function message(role,title,text,remember=true){const a=node('div','chat-message chat-'+role),icon=node('span','chat-avatar',role==='user'?'Lu':role==='tool'?'↳':'S');a.append(icon);const body=node('div','chat-bubble');body.append(node('div','chat-message-label',title));if(text)body.append(node('div','chat-prose',text));a.append(body);feed.append(a);if(remember&&current){current.events.push({role,title,text});persist();}bottom();return body;}
function table(parent,headers,rows){const wrap=node('div','chat-table-scroll'),tab=node('table'),head=node('thead'),hr=node('tr');headers.forEach(s=>hr.append(node('th','',s)));head.append(hr);tab.append(head);const body=node('tbody');rows.forEach(row=>{const tr=node('tr');row.forEach(v=>tr.append(node('td','',String(v))));body.append(tr);});tab.append(body);wrap.append(tab);parent.append(wrap);}
const fmt=(x,n=3)=>Number.isFinite(x)?x.toFixed(n):'—';
function renderOutput(action,o,body){
 if(o.error){body.append(node('p','chat-error',o.error));return;}
 if(action==='inspect_data'){
  table(body,[t('Measured collection','实测数据集'),t('Available records','可用记录')],[[t('Independent cells','独立电池'),o.cells.length],[t('Paired EIS / capacity records','配对 EIS / 容量记录'),o.pairedRecords],[t('EIS spectra connected to tools','已接入工具的 EIS 谱'),o.availableSpectra.length]]);
  body.append(node('p','chat-result-note',t('Cell identity and diagnostic indices are now available for the next tool call.','已取得电池编号和可用诊断序号，可据此选择下一次计算。')));
 }else if(action==='fit_eis'){
  body.append(node('p','chat-result-caption',o.cell+' · '+t('diagnostic index ','诊断序号 ')+o.cycle));
  table(body,[t('Numerical output','计算指标'),t('Value','数值')],[[t('Linear-KK RMS (reference)','线性 KK RMS（参考计算）'),o.linearKK?fmt(o.linearKK.rmsPercent,4)+'%':t('Not available','无参考结果')],[t('CPE weighted fit RMS','CPE 加权拟合 RMS'),fmt(o.weightedRmsPercent,4)+'%'],[t('Points used in fit','拟合点数'),o.fitPoints]]);
  if(o.referenceCandidates?.length)table(body,[t('Reference circuit','参考电路'),t('Weighted RMS','加权 RMS'),t('Successful starts','成功起点')],o.referenceCandidates.map(c=>[c.circuit,fmt(c.rmsPercent,4)+'%',c.starts]));
  table(body,[t('Fitted parameter','拟合参数'),t('Value','数值'),t('Unit','单位')],o.parameters.map(p=>[p.name,fmt(p.value,5),p.unit]));
  body.append(node('p','chat-result-note',t('KK is a precomputed impedance.py reference. Circuit fitting above ran now in a browser worker.','KK 来自预先计算的 impedance.py 参考；上方电路拟合已在浏览器后台线程实际执行。')));
 }else if(action==='simulate_pulse'){
  body.append(node('p','chat-result-note',t('One-RC forward simulation · illustrative parameters, not measured-cell identification.','一阶 RC 前向仿真 · 说明性参数，并非实测电池参数辨识。')));
  table(body,[t('Simulated quantity','仿真量'),t('Value','数值')],[[t('Instantaneous ohmic drop','瞬时欧姆压降'),fmt(o.instantaneousDropV*1000,3)+' mV'],[t('End of pulse voltage','脉冲末端电压'),fmt(o.endPulseVoltageV,6)+' V'],[t('End of rest voltage','静置末端电压'),fmt(o.endRestVoltageV,6)+' V']]);
  table(body,['t / s','I / A','V / V'],o.samples.filter((x,i)=>i%15===0||i===61).map(x=>[fmt(x.time_s,2),fmt(x.current_a,3),fmt(x.voltage_v,6)]));
 }else if(action==='predict_capacity'){
  table(body,[t('Comparator','比较方法'),'MAE / mAh'],[[t('EIS ridge','EIS 岭回归'),fmt(o.modelMAE,4)],[t('Persistence','沿用当前容量'),fmt(o.persistenceMAE,4)],[t('Training mean','训练均值'),fmt(o.baselineMAE,4)]]);
  body.append(node('p','chat-result-note',t('Train cells: ','训练电池：')+o.trainCells.join(', ')+ '\n'+t('Held-out test cells: ','独立测试电池：')+o.testCells.join(', ')));
 }
 const details=node('details','chat-raw'),summary=node('summary','',t('Full tool output (JSON)','完整工具返回值（JSON）'));details.append(summary,node('pre','',JSON.stringify(o,null,2)));body.append(details);
}
function showWelcome(){const body=message('assistant',t('SciencesLoop · analysis assistant','SciencesLoop · 分析助手'),t('Give me a battery question. I will recommend an applicable tool, show its actual output, and review the result before suggesting the next action. You can execute or modify every recommendation.','告诉我你要分析的电池问题。我会先推荐适用工具，展示实际返回值，再解释结果并提出下一步。每次调用前，你都可以执行或修改。'),false);const starter=node('div','chat-starters');examples.forEach(ex=>{const b=node('button','chat-starter',ex.title+' ↗');b.onclick=()=>sendTask(ex.task);starter.append(b);});body.append(starter);}
showWelcome();for(const r of records){for(const e of r.events||[]){const b=message(e.role,e.title,e.text,false);if(e.output)renderOutput(e.action,e.output,b);}}
function setStatus(s,running=false){clearInterval(statusTimer);const start=Date.now();const draw=()=>{$('agent-status').textContent=s+(running?' · '+Math.floor((Date.now()-start)/1000)+'s':'');};draw();if(running)statusTimer=setInterval(draw,1000);}
function setBusy(value){busy=value;$('agent-stop').hidden=!value;$('agent-run').disabled=value;$('agent-reset').disabled=value;$('agent-task').disabled=value;document.querySelectorAll('.chat-starter').forEach(b=>b.disabled=value);}
function disableProposal(note){if(pending?.element){pending.element.querySelectorAll('button,input,select').forEach(b=>b.disabled=true);pending.element.append(node('p','chat-result-note',note));}pending=null;}
function previousRound(){const prev=records.filter(r=>r!==current&&r.conclusion).at(-1);return prev?{task:prev.task,conclusion:prev.conclusion,nextSteps:prev.nextSteps,measurements:prev.steps.filter(s=>s.action!=='inspect_data').map(s=>({action:s.action,output:s.output}))}:null;}
async function loadData(){data??=await fetch(new URL('./data.json',import.meta.url),{signal:AbortSignal.timeout(20000)}).then(r=>{if(!r.ok)throw Error('Dataset HTTP '+r.status);return r.json();});return data;}
async function plan(){
 setBusy(true);const epoch=++runEpoch;setStatus(t('Model is reviewing the task and returned evidence','模型正在复核任务与已有结果'),true);
 const waiting=message('assistant',t('Analysis in progress','正在分析'),t('Waiting for the next model response…','正在等待模型返回下一步分析…'),false);waiting.classList.add('chat-pending');
 try{
  await loadData();requestAbort=new AbortController();const timeout=setTimeout(()=>requestAbort?.abort(),35000);let response;
  try{response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:current.task,dataset:{source:data.source,cells:data.audit.cells},selected:{cell:'25C05'},previous_round:previousRound(),history:current.history,user_feedback:current.feedback||[],interaction:'review-and-confirm'}),signal:requestAbort.signal});}finally{clearTimeout(timeout);}
  const out=await response.json();if(epoch!==runEpoch)return;if(!response.ok||out.mode!=='llm')throw Error(response.status===429?t('Request limit reached. Wait one minute, then retry.','请求达到限额，请稍等一分钟再重试。'):(out.error||'Model HTTP '+response.status));
  waiting.parentElement.remove();const s=out.step;current.model=out.model;
  if(s.result_summary&&current.history.length)message('assistant',t('Intermediate assessment','阶段小结'),s.result_summary);
  if(s.action==='finish'){
   if(!current.history.some(h=>h.output&&!h.output.error))throw Error(t('No successful tool result is available.','尚无成功的工具结果。'));
   current.conclusion=s.conclusion;current.nextSteps=s.next_steps;current.finishedAt=new Date().toISOString();message('assistant',t('Conclusion','本轮结论'),s.conclusion);
   if(s.next_steps?.length){const body=message('assistant',t('Next analysis or validation','下一轮分析或验证'),s.next_steps.map((x,i)=>(i+1)+'. '+x).join('\n'));const b=node('button','chat-starter',t('Review another EIS stage','继续分析另一阶段'));b.onclick=()=>{const example=t('Review the previous conclusion using cell 25C05 diagnostic index 275. Compare with the previous fit and state what changes. No prediction.','基于上一轮结论，分析电池 25C05 诊断序号 275 的 EIS，与上一轮结果比较并说明解释如何变化。不做预测。');$('agent-task').value=example;$('agent-task').focus();};body.append(b);}
   if(out.reviewWarnings?.length)message('assistant',t('Interpretation limits','解释边界'),out.reviewWarnings.join('\n'));
   persist();setBusy(false);setStatus(t('Round complete. Reply to continue the investigation.','本轮完成。可以继续追问，或提出下一轮分析任务。'));bottom();return;
  }
  if(!['inspect_data','fit_eis','predict_capacity','simulate_pulse'].includes(s.action))throw Error('Unsupported tool');
  const body=message('assistant',t('Recommended next action','推荐下一步'),s.explanation);const box=node('div','chat-action');box.append(node('div','chat-tool-name',labels[s.action]),node('code','chat-tool-code',s.action));
  const detail=node('details','chat-edit'),summary=node('summary','',t('Review or change parameters','查看或修改调用参数'));detail.append(summary);const fields=node('div','chat-parameter-fields');
  let cellSelect,indexSelect,horizonSelect,domainSelect;const simulationFields={};
  const field=(text,el)=>{const label=node('label','',text);label.append(el);fields.append(label);};
  const select=(values,selected)=>{const el=document.createElement('select');values.forEach(v=>{const o=node('option','',String(v));o.value=v;el.append(o);});el.value=selected;return el;};
  if(s.action==='fit_eis'){
   cellSelect=select([...new Set(data.spectra.map(x=>x.cell))],s.arguments.cell||'25C05');indexSelect=document.createElement('select');
   const indices=()=>{indexSelect.replaceChildren();data.spectra.filter(x=>x.cell===cellSelect.value).forEach(x=>{const o=node('option','',String(x.cycle));o.value=x.cycle;indexSelect.append(o);});if([...indexSelect.options].some(o=>Number(o.value)===s.arguments.cycle))indexSelect.value=s.arguments.cycle;};indices();cellSelect.onchange=indices;
   field(t('Cell','电池编号'),cellSelect);field(t('Diagnostic index','诊断序号'),indexSelect);detail.open=true;
  }else if(s.action==='predict_capacity'){horizonSelect=select([5,10,20],s.arguments.horizon||5);domainSelect=select(['25','35','45'],s.arguments.domain||'25');field(t('Horizon (diagnostic indices)','预测间隔（诊断序号）'),horizonSelect);field(t('Test temperature / °C','测试温度 / °C'),domainSelect);detail.open=true;}
  else if(s.action==='simulate_pulse'){for(const [name,label,def,min,max] of [['ocv_v','OCV / V',4,2.5,4.5],['current_a',t('Current / A','电流 / A'),.01,-.1,.1],['r0_ohm','R0 / Ω',.35,.001,20],['r1_ohm','R1 / Ω',.6,.001,20],['tau_s',t('Time constant / s','时间常数 / s'),10,.1,2000],['duration_s',t('Pulse duration / s','脉冲时长 / s'),10,1,300]]){const input=document.createElement('input');input.type='number';input.step='any';input.min=min;input.max=max;input.value=s.arguments[name]??def;simulationFields[name]=input;field(label,input);}fields.append(node('p','chat-result-note',t('Illustrative parameters. This forward simulation is not calibrated to the measured cell.','说明性参数。这是前向仿真，尚未对实测电池进行参数辨识。')));detail.open=true;}
  else fields.append(node('p','chat-result-note',t('Check cell inventory, alignment and available spectra in the connected public data.','检查已连接公开数据的电池目录、记录对齐和可用 EIS 谱。')));
  detail.append(fields);box.append(detail);
  const actions=node('div','chat-action-buttons'),execute=node('button','primary',t('Execute tool','执行这个工具')+' ↗'),revise=node('button','quiet',t('Change next action','调整下一步'));
  actions.append(execute,revise);box.append(actions);body.append(box);
  const getArgs=()=>({...s.arguments,...(cellSelect?{cell:cellSelect.value,cycle:Number(indexSelect.value)}:{}),...(horizonSelect?{horizon:Number(horizonSelect.value),domain:domainSelect.value}:{}),...Object.fromEntries(Object.entries(simulationFields).map(([k,v])=>[k,Number(v.value)]))});
  pending={step:s,element:box,getArgs};execute.onclick=()=>runTool(s,getArgs());revise.onclick=()=>{$('agent-task').focus();$('agent-task').placeholder=t('Tell me how to change the recommended next step…','告诉我希望怎样调整下一步，例如先分析序号 1…');};
  setBusy(false);setStatus(t('Choose Execute tool, edit its parameters, or reply with a different next step.','点击执行工具、修改参数，或在下方回复调整下一步。'));bottom();
  if($('agent-auto').checked)await runTool(s,getArgs(),true);
 }catch(e){if(epoch!==runEpoch)return;waiting.parentElement?.remove();setBusy(false);setStatus(t('Analysis paused: ','分析暂停：')+e.message);const body=message('assistant',t('Action needed','需要处理'),e.name==='AbortError'?t('The request timed out. No result was substituted. You can retry.','请求已超时，没有替代或模拟结果。可以重试。'):e.message);const b=node('button','chat-starter',t('Retry model response','重试模型调用'));b.onclick=()=>plan();body.append(b);}finally{if(epoch===runEpoch)requestAbort=null;}
}
async function runTool(s,args,automatic=false){
 if(busy)return;disableProposal(t('Action selected','已选择执行'));setBusy(true);const epoch=++runEpoch;
 message('user',automatic?t('Automatic execution enabled','本轮已开启自动执行'):t('Your action','你的操作'),t('Execute ','执行 ')+s.action+'\n'+JSON.stringify(args));
 const body=message('tool',t('Tool running','工具执行中')+' · '+s.action,t('Starting numerical computation…','正在启动实际计算…'));setStatus(t('Executing ','正在执行 ')+s.action,true);
 try{
  const key=s.action+JSON.stringify(args),existing=current.history.find(x=>x.key===key);let output;
  if(existing)output=existing.output;
  else output=await new Promise((resolve,reject)=>{worker=new Worker(new URL('./agent-worker.mjs',import.meta.url),{type:'module'});const timer=setTimeout(()=>{worker?.terminate();reject(Error(t('Tool execution timed out','工具执行超时')));},90000);worker.onmessage=({data:r})=>{clearTimeout(timer);worker.terminate();worker=null;r.error?reject(Error(r.error)):resolve(r.output);};worker.onerror=e=>{clearTimeout(timer);worker?.terminate();worker=null;reject(Error(e.message||'Worker failed'));};worker.postMessage({id:Date.now(),action:s.action,arguments:args});});
  if(epoch!==runEpoch)return;body.querySelector('.chat-message-label').textContent=t('Tool returned','工具已返回')+' · '+s.action;body.querySelector('.chat-prose')?.remove();renderOutput(s.action,output,body);
  const evt=current.events.at(-1);evt.title=t('Tool returned','工具已返回')+' · '+s.action;evt.text=existing?t('Reused the identical completed calculation.','复用相同参数的已完成计算。'):'';evt.action=s.action;evt.output=output;
  const item={key,tool:s.action,arguments:args,output};current.history.push(item);current.steps.push({action:s.action,arguments:args,output});persist();setBusy(false);bottom();await plan();
 }catch(e){if(epoch!==runEpoch)return;body.querySelector('.chat-message-label').textContent=t('Tool failed','工具执行失败');body.append(node('p','chat-error',e.message));current.history.push({tool:s.action,arguments:args,output:{error:e.message}});persist();setBusy(false);setStatus(t('Tool failed. Change the parameters or ask for another analysis.','工具未完成，可修改参数或请求其他分析。'));}finally{if(epoch===runEpoch)worker=null;}
}
async function sendTask(text){
 if(busy||!text.trim())return;activate();sticky=true;
 if(!current||current.conclusion){current={number:records.length+1,task:text.trim(),startedAt:new Date().toISOString(),events:[],steps:[],history:[],feedback:[],conclusion:'',nextSteps:[]};records.push(current);}
 else{disableProposal(t('Superseded by your new instruction','已按你的新要求重新规划'));current.feedback.push(text.trim());current.feedback=current.feedback.slice(-4);}
 message('user',t('You','你'),text.trim());$('agent-task').value='';bottom(true);await plan();
}
$('agent-form').onsubmit=e=>{e.preventDefault();sendTask($('agent-task').value);};$('agent-task').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();sendTask($('agent-task').value);}};
$('agent-auto').onchange=()=>{if($('agent-auto').checked&&pending&&!busy)runTool(pending.step,pending.getArgs(),true);};
$('agent-stop').onclick=()=>{++runEpoch;requestAbort?.abort();worker?.terminate();worker=null;setBusy(false);disableProposal(t('Stopped by researcher','已由研究人员停止'));message('assistant',t('Stopped','已停止'),t('Completed results remain in the conversation. Reply to revise the next step.','已完成的结果保留在对话中。可以回复调整下一步。'));setStatus(t('Stopped. No pending tool will execute.','已停止，待执行工具不会继续运行。'));};
$('agent-reset').onclick=()=>{if(busy)return;disableProposal('');records=[];current=null;try{sessionStorage.removeItem('sciencesloop-battery-chat-v2');}catch{}feed.replaceChildren();showWelcome();setStatus('');};
$('agent-export').onclick=()=>{const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify({schema:'battery-agent-chat-v2',records},null,2)],{type:'application/json'}));a.href=url;a.download='battery-agent-conversation.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('agent-catalog-toggle').onclick=()=>{$('agent-catalog').hidden=!$('agent-catalog').hidden;};
async function catalog(){try{const c=await fetch(new URL('./agent-workflows.json',import.meta.url)).then(r=>r.json());const box=$('agent-catalog');const report=node('a','chat-source',t('Read the full scenario and tool study','阅读完整场景与工具研究'));report.href=new URL('./agent-workflows.md',import.meta.url).href;report.target='_blank';box.append(report);box.append(node('h3','',t('Choose tools by question and evidence','按研究问题与数据条件选择工具')));c.scenarios.forEach(s=>{const d=node('details','chat-scenario'),summary=node('summary','',(zh?s.title.zh:s.title.en)+' · '+(s.status==='live'?t('Live','已接入'):t('Research / integration','研究与待接入')));d.append(summary,node('p','',zh?s.inputs.zh:s.inputs.en),node('p','',s.tools.join(' → ')),node('p','chat-result-note',zh?s.gate.zh:s.gate.en));s.sources.forEach(src=>{const a=node('a','chat-source',src.label);a.href=src.url;a.target='_blank';a.rel='noreferrer';d.append(a);});box.append(d);});}catch{$('agent-catalog').textContent=t('Scenario catalog unavailable. EIS and prediction tools remain available.','场景目录暂时未加载；EIS 和预测工具仍可使用。');}}
catalog();
loadData().then(()=>{$('agent-connection').textContent=t('Public measurements connected · model selected tools','公开实测数据已连接 · 模型选择工具');}).catch(e=>{$('agent-connection').textContent=t('Dataset unavailable: ','数据未加载：')+e.message;});
