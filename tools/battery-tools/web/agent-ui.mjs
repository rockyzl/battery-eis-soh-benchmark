import {fitCircuit,investigate} from '../analysis.mjs';
const endpoint='https://api.sciencesloop.com/api/battery-agent';
const zh=new URLSearchParams(location.search).get('lang')!=='en';
const t=(a,b)=>zh?b:a;
const panel=document.createElement('section');panel.className='panel';panel.id='research-agent';
panel.innerHTML=`<div class="eyebrow">BATTERY RESEARCH AGENT</div><h2>${t('Electrochemical data analysis task','电化学数据分析任务')}</h2><p class="muted">${t('The model selects tools, receives measured numerical results, and writes a conclusion. You decide what to do next.','模型根据任务选择工具，读取实际计算结果，再分析并给出结论。每轮由你决定下一步。')}</p><label>${t('Research task','研究任务')}<textarea id="agent-task" rows="4" style="display:block;width:100%;padding:14px;margin:10px 0;border:1px solid #abbcaf;background:#fffef9;font:inherit"></textarea></label><div class="controls"><button class="primary" id="agent-run">${t('Analyze data','开始分析')}</button><button class="quiet" id="agent-next">${t('Next iteration','基于本轮开始下一次分析')}</button><button class="quiet" id="agent-export">${t('Export record','导出分析记录')}</button><button class="quiet" id="agent-reset">${t('Clear rounds','清空轮次')}</button></div><p class="small">${t('Your task and public-data tool results are sent to the SciencesLoop model backend. No uploaded private file is sent by this version. No arbitrary code or experiment execution.','任务文字和公开数据的计算结果会发送到 SciencesLoop 模型后端。本版本不会向后端发送上传的私人文件，也不执行任意代码或实验。')}</p><div role="status" aria-live="polite" id="agent-status"></div><div id="agent-rounds"></div>`;
document.querySelector('.content').prepend(panel);
const nav=document.createElement('button');nav.className='tab active';nav.dataset.tab='research-agent';nav.textContent=t('Research task','研究任务');document.querySelector('aside').prepend(nav);
const activate=()=>{document.querySelectorAll('.panel').forEach(p=>p.hidden=p!==panel);document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b===nav));};nav.onclick=activate;activate();
const $=id=>document.getElementById(id);$('agent-task').value=t('Analyze the EIS spectra of cell 25C05. Check consistency and compare equivalent-circuit fits. Explain what can be concluded and suggest a validation measurement. Do not build a prediction model.','分析电池 25C05 的 EIS 数据。先检查数据一致性，再比较等效电路拟合，说明哪些结论有依据、哪些仍不能确定，并建议下一步验证。此任务不需要预测模型。');
let data,rounds=[];try{rounds=JSON.parse(sessionStorage.getItem('sciencesloop-battery-rounds')||'[]');}catch{}
const log=(article,title,body)=>{const p=document.createElement('div');p.style.cssText='padding:15px 0;border-bottom:1px solid #d9dfd7';const h=document.createElement('strong');h.textContent=title;p.append(h);const pre=document.createElement('pre');pre.style.whiteSpace='pre-wrap';pre.textContent=typeof body==='string'?body:JSON.stringify(body,null,2);if(typeof body==='string')p.append(pre);else{const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent=t('Inspect numerical output','查看数值结果与参数');details.append(summary,pre);p.append(details);}article.append(p);};
function showSaved(){for(const r of rounds){const a=document.createElement('article');a.className='full';log(a,t('Previous round','已完成轮次')+' '+r.number,r.task);log(a,t('Conclusion','结论'),r.conclusion);$('agent-rounds').append(a);}}showSaved();
function persist(){sessionStorage.setItem('sciencesloop-battery-rounds',JSON.stringify(rounds.slice(-6)));}
$('agent-next').onclick=()=>{if(!rounds.length)return;$('agent-task').value=t('Review the previous conclusion using another measured diagnostic stage for the same cell. State whether the interpretation changes and what evidence is still missing.','依据上一轮结论，分析同一电池另一诊断阶段的实测 EIS。检查原先解释是否仍成立，并说明还缺少什么证据。');$('agent-task').focus();};
$('agent-reset').onclick=()=>{rounds=[];sessionStorage.removeItem('sciencesloop-battery-rounds');$('agent-rounds').replaceChildren();$('agent-status').textContent='';};
$('agent-export').onclick=()=>{const a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify({schema:'battery-agent-rounds-v1',rounds},null,2)],{type:'application/json'}));a.href=url;a.download='battery-agent-rounds.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
async function tool(action,args){
 if(action==='inspect_data')return {source:data.source,cells:data.audit.cells,pairedRecords:data.samples.length,audit:data.audit,availableSpectra:data.spectra.map(s=>({cell:s.cell,cycle:s.cycle})),limitations:['Only public measured collection is connected.','No literature search or hardware control tool is available.']};
 if(action==='fit_eis'){
  const cell=args.cell||'25C05',available=data.spectra.filter(s=>s.cell===cell);const s=available.find(s=>s.cycle===args.cycle)||(!args.cycle?available[0]:null);if(!s)throw Error('Requested spectrum unavailable; consult inspect_data spectrum inventory');
  const fit=fitCircuit(data.frequency_hz,s.real,s.negImag),ref=data.references.find(r=>r.cell===s.cell&&r.cycle===s.cycle);
  return {cell:s.cell,cycle:s.cycle,circuit:'R0-p(R1,CPE1)-W1',engine:'browser numerical optimizer',weightedRmsPercent:fit.weightedRmsPercent,fitPoints:fit.fitPoints,parameters:fit.names.map((name,i)=>({name,value:fit.parameters[i],unit:fit.units[i]})),linearKK:ref?{engine:'impedance.py 1.7.1 reference calculation',rmsPercent:ref.kk.rms_percent,M:ref.kk.M}:null,referenceCandidates:ref?.models.map(m=>({circuit:m.circuit,rmsPercent:m.best.weighted_rms_percent,aic:m.best.aic,starts:m.successful_starts})),limits:'A lower fit error does not establish a unique mechanism. Browser fitting does not execute KK; reference results only exist for listed spectra.'};
 }
 if(action==='predict_capacity'){const r=investigate(data,{horizon:args.horizon||5,domain:args.domain||'25'});return {horizon:r.horizon,trainCells:r.trainCells,testCells:r.testCells,modelMAE:r.metrics.mae,persistenceMAE:r.persistence.mae,baselineMAE:r.baseline.mae,unit:'mAh',alpha:r.alpha,limits:r.limitations};}
 throw Error('Tool not allowed');
}
$('agent-run').onclick=async()=>{
 const task=$('agent-task').value.trim();if(!task)return;
 $('agent-run').disabled=true;$('agent-next').disabled=true;
 const article=document.createElement('article');article.className='full';$('agent-rounds').prepend(article);
 const round={number:rounds.length+1,task,startedAt:new Date().toISOString(),steps:[],conclusion:'',nextSteps:[]};log(article,t('Research task','研究任务')+' '+round.number,task);
 try{
  data??=await fetch(new URL('./data.json',import.meta.url)).then(r=>{if(!r.ok)throw Error('Dataset load failed');return r.json();});
  const history=[],seen=new Set();
  for(let i=0;i<7;i++){
   $('agent-status').textContent=t('Model selecting the next analysis…','模型正在选择下一步分析…');
   const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task,dataset:{source:data.source,cells:data.audit.cells},selected:{cell:'25C05'},previous_round:rounds.at(-1)?{task:rounds.at(-1).task,conclusion:rounds.at(-1).conclusion,nextSteps:rounds.at(-1).nextSteps,measurements:rounds.at(-1).steps.filter(x=>x.action==='fit_eis').map(x=>x.output)}:null,history}),signal:AbortSignal.timeout(55000)});
   const out=await response.json();if(!response.ok||out.mode!=='llm')throw Error(out.error||'Model connection unavailable');
   const s=out.step;if(!['inspect_data','fit_eis','predict_capacity','finish'].includes(s.action))throw Error('Unsupported model action');
   log(article,t('Model choice','模型选择')+': '+s.action,s.explanation);
   if(s.action==='finish'){
    if(!history.some(h=>h.output&&!h.output.error))throw Error('Model finished without any successful tool result');
    round.conclusion=s.conclusion;round.reviewWarnings=out.reviewWarnings||[];if(round.reviewWarnings.length)log(article,t('Scientific review flag','科学解释复核提示'),round.reviewWarnings.join('\n'));round.nextSteps=s.next_steps;round.model=out.model;round.finishedAt=new Date().toISOString();log(article,t('Conclusion','结论'),s.conclusion);log(article,t('Suggested next analysis or measurement','下一轮分析或验证建议'),s.next_steps.join('\n'));rounds.push(round);persist();$('agent-status').textContent=t('Round complete. Researcher decides the next action.','本轮分析完成。由研究人员决定下一步；可补充任务开始下一轮。');return;
   }
   if(i===0&&s.action!=='inspect_data')throw Error('Data inspection must precede calculation');
   const key=s.action+JSON.stringify(s.arguments);if(seen.has(key)){const prior=history.find(h=>h.tool===s.action&&JSON.stringify(h.arguments)===JSON.stringify(s.arguments));history.push({tool:s.action,arguments:s.arguments,output:prior?.output||{error:'Tool result already returned'},notice:'Duplicate request: reuse the existing output; select finish or a different analysis.'});log(article,t('Existing tool result reused','复用已完成的工具结果'),t('No duplicate calculation was run. The model must review the returned evidence.','未重复执行计算。模型将依据已返回结果继续汇总。'));continue;}seen.add(key);
   $('agent-status').textContent=t('Executing numerical tool: ','正在执行数值工具：')+s.action;
   let output;try{output=await tool(s.action,s.arguments);}catch(e){output={error:e.message};}
   history.push({tool:s.action,arguments:s.arguments,output});round.steps.push({action:s.action,explanation:s.explanation,arguments:s.arguments,output,response_id:out.response_id});log(article,t('Actual tool result','实际工具结果'),output);
  }
  throw Error('Step budget reached. No conclusion was fabricated. Narrow the task and retry.');
 }catch(e){round.error=e.message;log(article,t('Analysis stopped','分析中止'),e.message);$('agent-status').textContent=e.message;}finally{$('agent-run').disabled=false;$('agent-next').disabled=false;}
};
