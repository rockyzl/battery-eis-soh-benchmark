import {investigate,fitCircuit,parseCSV,cyclingAnalysis,mean} from '../analysis.mjs';
const $=id=>document.getElementById(id),lang=new URLSearchParams(location.search).get('lang'),zh=lang?lang==='zh':location.pathname.startsWith('/zh/');
const t=(en,cn)=>zh?cn:en,esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const colors=['#245c51','#d5613e','#7396ad','#b49453','#7d7395','#8b645a'];
let data,result,currentFit=null;
const num=(x,n=2)=>Number.isFinite(x)?x.toFixed(n):'—';
function download(name,text,type='text/plain'){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([text],{type}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);}
function table(headers,rows){return '<div class="table-scroll"><table><thead><tr>'+headers.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(x=>'<td>'+x+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';}
function chart(id,series,{xlabel='',ylabel='',scatter=false,equal=false,diagonal=false,band=null}={}){
  const all=series.flatMap(s=>s.points).filter(p=>p.every(Number.isFinite));
  if(!all.length){$(id).textContent=t('No eligible points','没有满足条件的数据点');return;}
  const w=510,h=300,L=53,R=18,T=16,B=46;
  let xmin=Math.min(...all.map(p=>p[0])),xmax=Math.max(...all.map(p=>p[0])),ymin=Math.min(...all.map(p=>p[1])),ymax=Math.max(...all.map(p=>p[1]));
  if(band){ymin=Math.min(ymin,...band.lower.map(p=>p[1]));ymax=Math.max(ymax,...band.upper.map(p=>p[1]));}
  if(diagonal){xmin=ymin=Math.min(xmin,ymin);xmax=ymax=Math.max(xmax,ymax);}
  let dx=xmax-xmin||1,dy=ymax-ymin||1;xmin-=dx*.04;xmax+=dx*.04;ymin-=dy*.08;ymax+=dy*.08;
  if(equal){const range=Math.max(xmax-xmin,(ymax-ymin)*(w-L-R)/(h-T-B)),cx=(xmin+xmax)/2,cy=(ymin+ymax)/2;xmin=cx-range/2;xmax=cx+range/2;const yr=range*(h-T-B)/(w-L-R);ymin=cy-yr/2;ymax=cy+yr/2;}
  const X=x=>L+(x-xmin)/(xmax-xmin)*(w-L-R),Y=y=>h-B-(y-ymin)/(ymax-ymin)*(h-T-B);
  let svg='<svg viewBox="0 0 '+w+' '+h+'" role="img" aria-label="'+esc(xlabel+' versus '+ylabel)+'">';
  for(let i=0;i<=4;i++){const x=xmin+(xmax-xmin)*i/4,y=ymin+(ymax-ymin)*i/4;
    svg+='<line x1="'+L+'" x2="'+(w-R)+'" y1="'+Y(y)+'" y2="'+Y(y)+'" stroke="#e4e8de"/><text x="'+(L-8)+'" y="'+(Y(y)+3)+'" text-anchor="end" fill="#66796d" font-size="10">'+num(y,Math.abs(y)<1?3:1)+'</text><text x="'+X(x)+'" y="'+(h-B+18)+'" text-anchor="middle" fill="#66796d" font-size="10">'+num(x,Math.abs(x)<1?2:1)+'</text>';}
  if(diagonal)svg+='<line x1="'+X(xmin)+'" y1="'+Y(xmin)+'" x2="'+X(xmax)+'" y2="'+Y(xmax)+'" stroke="#a0aca1" stroke-dasharray="5 5"/>';
  const path=points=>points.map((p,i)=>(i?'L':'M')+num(X(p[0]),2)+','+num(Y(p[1]),2)).join(' ');
  if(band)svg+='<path d="'+path([...band.upper,...[...band.lower].reverse()])+'Z" fill="#d5613e" opacity=".10"/>';
  series.forEach((s,j)=>{const c=s.color||colors[j%colors.length],p=s.points.filter(p=>p.every(Number.isFinite));
    if(!(scatter||s.scatter))svg+='<path d="'+path(p)+'" fill="none" stroke="'+c+'" stroke-width="2" '+(s.dashed?'stroke-dasharray="5 3"':'')+'/>';
    if(scatter||s.scatter||p.length<65)svg+=p.map(p=>'<circle cx="'+X(p[0])+'" cy="'+Y(p[1])+'" r="'+(scatter?2.3:2.1)+'" fill="'+c+'" opacity=".72"><title>'+esc(s.name)+': '+num(p[0],3)+', '+num(p[1],3)+'</title></circle>').join('');});
  svg+='<text x="'+((L+w-R)/2)+'" y="'+(h-5)+'" text-anchor="middle" font-size="11" fill="#516b5d">'+esc(xlabel)+'</text><text x="13" y="'+((T+h-B)/2)+'" text-anchor="middle" transform="rotate(-90 13 '+((T+h-B)/2)+')" font-size="11" fill="#516b5d">'+esc(ylabel)+'</text></svg>';
  $(id).innerHTML=svg+'<div class="legend">'+series.map((s,i)=>'<span><i style="background:'+(s.color||colors[i%colors.length])+'"></i>'+esc(s.name)+'</span>').join('')+'</div>';
}
function selectTab(id){document.querySelectorAll('.panel').forEach(p=>p.hidden=p.id!==id);document.querySelectorAll('.tab').forEach(b=>{b.classList.toggle('active',b.dataset.tab===id);b.setAttribute('aria-pressed',String(b.dataset.tab===id));});}
document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>selectTab(b.dataset.tab)));
$('language').onclick=()=>{const u=new URL(location.href);u.searchParams.set('lang',zh?'en':'zh');location.href=u;};
function showCapacity(){if(!result)return;const cell=$('resultCell').value,rows=result.predictions.filter(r=>r.cell===cell).sort((a,b)=>a.targetCycle-b.targetCycle);
  chart('capacityChart',[{name:t('Measured','实测'),points:rows.map(r=>[r.targetCycle,r.target])},{name:t('EIS prediction','EIS 预测'),points:rows.map(r=>[r.targetCycle,r.prediction]),dashed:true},{name:t('Persistence','沿用当前容量'),points:rows.map(r=>[r.targetCycle,r.persistence])}],{xlabel:t('Target diagnostic index','目标诊断序号'),ylabel:t('Capacity (mAh)','容量 (mAh)'),band:{upper:rows.map(r=>[r.targetCycle,r.upper]),lower:rows.map(r=>[r.targetCycle,r.lower])}});
}
$('resultCell').onchange=showCapacity;
$('run').onclick=()=>{
  try{
    $('run').disabled=true;const start=performance.now();
    result=investigate(data,{horizon:Number($('horizon').value),domain:$('domain').value});
    result.runtimeMs=performance.now()-start;result.source=data.source;result.generatedAt=new Date().toISOString();
    $('results').hidden=false;$('empty').hidden=true;$('downloadReport').disabled=false;
    const r=result,better=r.metrics.mae<r.persistence.mae;
    $('findingTitle').textContent=better?t('EIS improves on carrying capacity forward in this test','这次检验中，EIS 优于沿用当前容量'):t('The simple baseline remains the stronger forecast','在这次检验中，简单基线更可靠');
    $('findingText').textContent=t('At a horizon of '+r.horizon+' diagnostic indices, the EIS ridge model reaches '+num(r.metrics.mae)+' mAh mean cell MAE, versus '+num(r.persistence.mae)+' mAh for persistence, across '+r.metrics.cells+' held-out cells. This is an exploratory result, not proof of a mechanism or universal transfer.',
      '预测 '+r.horizon+' 个诊断序号之后的容量：EIS 岭回归的平均单电池 MAE 为 '+num(r.metrics.mae)+' mAh，沿用当前容量的误差为 '+num(r.persistence.mae)+' mAh，共检验 '+r.metrics.cells+' 个留出电池。这是探索性结果，不能据此证明退化机制或通用迁移能力。');
    $('metrics').innerHTML=[[t('EIS prediction MAE','EIS 预测误差'),num(r.metrics.mae)+' mAh'],[t('Persistence MAE','沿用当前容量误差'),num(r.persistence.mae)+' mAh'],[t('Independent test cells','独立测试电池'),String(r.metrics.cells)]].map(([label,v])=>'<div class="metric"><small>'+esc(label)+'</small><strong>'+esc(v)+'</strong></div>').join('');
    $('resultCell').innerHTML=r.testCells.map(c=>'<option>'+esc(c)+'</option>').join('');showCapacity();
    chart('scatterChart',r.testCells.map(cell=>({name:cell,points:r.predictions.filter(p=>p.cell===cell).map(p=>[p.target,p.prediction])})),{xlabel:t('Measured capacity (mAh)','实测容量 (mAh)'),ylabel:t('Predicted capacity (mAh)','预测容量 (mAh)'),scatter:true,diagonal:true});
    $('modelTable').innerHTML=table([t('Model','模型'),'MAE (mAh)',t('What it uses','使用的信息')],[
      [t('Training mean','训练集均值'),num(r.baseline.mae),t('Development target mean only','仅开发集目标均值')],
      [t('Persistence','沿用当前容量'),num(r.persistence.mae),t('Last measured capacity at prediction cutoff','预测时刻已测得的容量')],
      ['EIS Ridge',num(r.metrics.mae),t('Ten EIS features; alpha '+r.alpha+' selected on development cells','10 个 EIS 特征；仅开发集选择 alpha='+r.alpha)]
    ]);
    chart('cvChart',[{name:t('Cell-grouped CV','按电池分组验证'),points:r.cv.map(x=>[Math.log10(x.alpha),x.mae])}],{xlabel:'log10(alpha)',ylabel:'MAE (mAh)'});
    $('nextExperiment').textContent=t('Repeat EIS at the same state of charge, temperature and rest duration, paired with a capacity diagnostic. Check reproducibility and excitation-amplitude dependence before assigning a mechanism. Predefine the forecast horizon and compare EIS against the persistence baseline on new cells.',
      '在相同荷电状态、温度和静置时间下重复 EIS，并配对容量诊断。先检查重复性及激励幅值的影响，再讨论物理机制。预先固定预测范围，在新电池上与沿用当前容量的基线比较。');
    const top=r.correlations[0],f=data.feature_frequencies_hz[top.feature%5],component=top.feature<5?'Re(Z)':'−Im(Z)';
    const steps=[
      [t('Inspect source and align measurements','核对来源并对齐测量'),t(data.samples.length+' valid records from '+data.audit.cells.length+' cells. EIS k pairs with capacity k−1. '+Object.values(data.audit.missing_capacity_by_cell).reduce((a,b)=>a+b,0)+' spectra without matched capacity were excluded.',data.samples.length+' 条有效记录，来自 '+data.audit.cells.length+' 个电池；EIS k 对齐容量 k−1。排除 '+Object.values(data.audit.missing_capacity_by_cell).reduce((a,b)=>a+b,0)+' 条缺少配对容量的谱。')],
      [t('Define prediction before opening test outcomes','明确预测目标'),t('Use EIS at k to predict measured capacity at k+'+r.horizon+'; '+r.droppedPairs+' records have no exact future label and are excluded.', '用 k 时刻的 EIS 预测 k+'+r.horizon+' 的实测容量；'+r.droppedPairs+' 条记录缺少精确对应的未来标签，已排除。')],
      [t('Separate complete cells','按完整电池划分'),t('Development: '+r.trainCells.join(', ')+'. Test: '+r.testCells.join(', ')+'. '+r.trainN+' development rows, '+r.testN+' test rows.','开发集：'+r.trainCells.join(', ')+'；测试集：'+r.testCells.join(', ')+'。开发记录 '+r.trainN+'，测试记录 '+r.testN+'。')],
      [t('Develop fixed electrochemical features','构建固定电化学特征'),t('Real and negative-imaginary impedance at five prespecified frequencies. Strongest development-row correlation: '+component+' near '+num(f,2)+' Hz, r='+num(top.r)+'. Repeated rows are not independent evidence.', '在预先固定的 5 个频率提取实部和负虚部。开发记录中最强相关：'+component+'，约 '+num(f,2)+' Hz，r='+num(top.r)+'。同电池重复记录不是独立证据。')],
      [t('Choose regularization without test cells','仅用开发集选择正则化'),t('Standardization refits inside every cell fold. Alpha '+r.alpha+' wins grouped development CV, MAE '+num(r.cvMae)+' mAh.', '每个电池折内部重新计算标准化。按电池分组验证选择 alpha='+r.alpha+'，开发误差 '+num(r.cvMae)+' mAh。')],
      [t('Evaluate and challenge the result','检验并质疑结果'),t('Test MAE '+num(r.metrics.mae)+' mAh. Persistence '+num(r.persistence.mae)+' mAh. '+r.outliers+' test rows exceed a 5-SD feature check. Exploratory band coverage '+num(100*r.coverage,1)+'%.', '测试 MAE='+num(r.metrics.mae)+' mAh；沿用当前容量='+num(r.persistence.mae)+' mAh。'+r.outliers+' 条测试记录触发 5 标准差特征检查；探索性区间覆盖率 '+num(100*r.coverage,1)+'%。')],
      [t('Record a reproducible next step','保留可重现的下一步'),t('Export inputs, split, model coefficients, predictions and limitations. Runtime '+num(r.runtimeMs,0)+' ms. The workflow is deterministic, not a live LLM transcript.', '可导出输入来源、划分、模型系数、预测和限制。耗时 '+num(r.runtimeMs,0)+' ms。此记录来自确定性计算流程，不是实时大模型对话。')]
    ];
    $('trace').innerHTML=steps.map(([a,b])=>'<li><b>'+esc(a)+'</b><span>'+esc(b)+'</span></li>').join('');
    $('modelCode').textContent=JSON.stringify({model:r.model,trainCells:r.trainCells,testCells:r.testCells,horizon:r.horizon,cv:r.cv,limitations:r.limitations},null,2);
    $('status').textContent=t('Investigation complete · '+num(r.runtimeMs,0)+' ms · all predictions computed locally','计算完成 · '+num(r.runtimeMs,0)+' 毫秒 · 所有预测均在本地计算');
    window.sciencesloopResult=r;
  }catch(e){$('status').textContent=e.message;$('status').className='error';}finally{$('run').disabled=false;}
};
$('downloadReport').onclick=()=>download('sciencesloop-battery-investigation.json',JSON.stringify(result,null,2),'application/json');
$('downloadPredictions').onclick=()=>{if(!result)return;const cols=['cell','cycle','targetCycle','capacity','target','prediction','persistence','lower','upper'];download('battery-predictions.csv',cols.join(',')+'\n'+result.predictions.map(r=>cols.map(c=>r[c]).join(',')).join('\n'),'text/csv');};
function selectedSpectrum(){return data.spectra.find(s=>s.cell===$('eisCell').value&&s.cycle===Number($('eisCycle').value));}
function drawEis(){const s=selectedSpectrum();if(!s)return;currentFit=null;const reference=data.references.find(r=>r.cell===s.cell&&r.cycle===s.cycle);
  const series=[{name:t('Measured','实测'),points:s.real.map((v,i)=>[v,s.negImag[i]]),scatter:true}];
  if(reference)series.push({name:'lin-KK',points:reference.kk.real.map((v,i)=>[v,reference.kk.negImag[i]]),color:'#7396ad'});
  chart('nyquist',series,{xlabel:'Re(Z) / Ω',ylabel:'−Im(Z) / Ω',equal:true});
  chart('bode',[{name:'|Z|',points:s.real.map((v,i)=>[Math.log10(data.frequency_hz[i]),Math.hypot(v,s.negImag[i])]).reverse()}],{xlabel:'log10(f / Hz)',ylabel:'|Z| / Ω'});
  $('eisChecks').textContent=t('60 finite, positive-frequency points. Source column is −Im(Z), converted to Z = Re(Z) − j·(−Im(Z)) for reference calculations. '+(reference?'Linear-KK RMS residual '+num(reference.kk.rms_percent,3)+'%, M='+reference.kk.M+'.':'No precomputed linear-KK result for this spectrum; circuit fitting does not replace validation.'),
    '60 个有限值、正频率点。源文件为 −Im(Z)，计算时明确转换符号。'+(reference?'线性 KK 的 RMS 残差为 '+num(reference.kk.rms_percent,3)+'%，M='+reference.kk.M+'。':'此谱没有预计算的线性 KK 结果；拟合不能替代数据验证。'));
  $('fitResults').innerHTML=reference?'<article class="full"><h3>'+t('impedance.py reference comparison','impedance.py 参考比较')+'</h3>'+table([t('Candidate','候选电路'),t('Weighted RMS','加权 RMS'), 'AIC', t('Starts','初值次数')],reference.models.map(m=>[esc(m.name),num(m.best.weighted_rms_percent,3)+'%',num(m.best.aic,1),String(m.successful_starts)]))+'<p class="small">'+t('Same capacitive fit points and modulus weighting. Numerical fit quality alone does not establish a unique mechanism.','使用相同容性点与模值加权。数值拟合质量不能证明唯一机制。')+'</p></article>':'';
}
function populateCycles(){const a=data.spectra.filter(s=>s.cell===$('eisCell').value);$('eisCycle').innerHTML=a.map(s=>'<option>'+s.cycle+'</option>').join('');drawEis();}
$('eisCell').onchange=populateCycles;$('eisCycle').onchange=drawEis;
$('fit').onclick=()=>{try{const s=selectedSpectrum(),start=performance.now();currentFit=fitCircuit(data.frequency_hz,s.real,s.negImag);
  chart('nyquist',[{name:t('Measured','实测'),points:s.real.map((v,i)=>[v,s.negImag[i]]),scatter:true},{name:t('CPE candidate','CPE 候选'),points:currentFit.real.map((v,i)=>[v,currentFit.negImag[i]])}],{xlabel:'Re(Z) / Ω',ylabel:'−Im(Z) / Ω',equal:true});
  $('fitResults').insertAdjacentHTML('afterbegin','<article class="full browser-fit"><h3>'+t('Browser candidate fit','浏览器候选拟合')+'</h3><p>'+t('Weighted RMS residual','加权 RMS 残差')+': '+num(currentFit.weightedRmsPercent,3)+'% · '+currentFit.fitPoints+' '+t('capacitive points','个容性点')+' · '+num(performance.now()-start,0)+' ms</p>'+table([t('Parameter','参数'),t('Value','数值'),t('Unit','单位')],currentFit.names.map((n,i)=>[n,currentFit.parameters[i].toPrecision(5),currentFit.units[i]]))+'<p class="small">'+t('Independent browser optimizer; not the impedance.py solver. Candidate parameters need sensitivity and identifiability review.','独立浏览器优化器，并非 impedance.py 求解器。参数仍需敏感性与可辨识性检查。')+'</p></article>');
  const fits=document.querySelectorAll('.browser-fit');fits.forEach((el,i)=>{if(i>0)el.remove();});
}catch(e){$('fitResults').textContent=e.message;}};
function toolsTable(){const tools=[
  ['cellpy','https://cellpy.readthedocs.io/',t('Cycler import, cycle/step summaries, batch processing','循环仪数据导入、循环和步骤汇总、批处理'),t('Units, step classification, capacity resets','单位、步骤识别、容量归零规则')],
  ['PyProBE','https://pyprobe.readthedocs.io/',t('Battery experiment processing, ICA/DVA, degradation analysis','电池数据处理、ICA/DVA、退化分析'),t('Protocol context, smoothing, electrode reference curves','测试协议、平滑方法、电极参考曲线')],
  ['galvani','https://github.com/echemdata/galvani',t('Vendor-native electrochemical file parsing','仪器原生电化学文件解析'),t('Format/firmware version, dependencies, channel semantics','格式与固件版本、依赖、通道含义')],
  ['impedance.py','https://impedancepy.readthedocs.io/',t('Linear-KK and equivalent-circuit fitting','线性 KK 检查、等效电路拟合'),t('Complex sign, weighting, initial guesses, nonunique models','虚部符号、权重、初始值、模型不唯一')],
  ['pyimpspec','https://vyrjana.github.io/pyimpspec/',t('KK/Z-HIT, circuit fitting and DRT','KK/Z-HIT、电路拟合与 DRT'),t('Regularization and validation before interpreting DRT peaks','正则化、DRT 峰解释之前的数据验证')],
  ['ZView','https://www.ameteksi.com/products/software/zview-software-en',t('Commercial EIS fitting and simulation','商业 EIS 拟合与仿真'),t('License, circuit judgment, reproducible exports','许可、电路选择、可重现导出')],
  ['PyBaMM + PyBOP','https://github.com/pybop-team/PyBOP',t('Physics-based battery simulation and parameter fitting','物理电池模型仿真与参数辨识'),t('Geometry, chemistry, parameter identifiability, solver cost','几何、化学体系、参数可辨识性、求解成本')],
  ['PBT','https://github.com/Ruifeng-Tan/PBT',t('Pretrained battery lifetime prediction','预训练电池寿命预测'),t('Matching inputs/labels, target adaptation, cell-held-out tests','匹配输入与标签、目标域适配、按电池留出验证')]
];$('toolTable').innerHTML=table([t('Tool','工具'),t('Useful calculation','可用计算'),t('What needs expertise','需要专业判断的环节')],tools.map(([n,u,a,b])=>['<a href="'+u+'" target="_blank" rel="noreferrer">'+n+' ↗</a>',esc(a),esc(b)]));}
$('templateEis').onclick=()=>{const s=data.spectra.find(s=>s.cell==='25C05');download('measured-eis-example.csv','cell_id,cycle,frequency_hz,z_real_ohm,z_imag_ohm\n'+data.frequency_hz.map((f,i)=>[s.cell,s.cycle,f,s.real[i],-s.negImag[i]].join(',')).join('\n'),'text/csv');};
$('templateCycling').onclick=()=>download('cycling-column-template.csv','cell_id,cycle,voltage_v,capacity_mah,current_a\n','text/csv');
$('upload').onchange=async()=>{try{const file=$('upload').files[0];if(!file)return;if(file.size>10*1024*1024)throw Error(t('Maximum file size is 10 MB','文件不能超过 10 MB'));
  const rows=parseCSV(await file.text());$('importResults').replaceChildren();if(!rows.length)throw Error('No data rows');
  if('frequency_hz' in rows[0]){
    const required=['cell_id','cycle','frequency_hz','z_real_ohm','z_imag_ohm'];if(required.some(k=>!(k in rows[0])))throw Error('Required: '+required.join(', '));
    const groups=new Map();for(const r of rows){const key=r.cell_id+':'+r.cycle;if(!groups.has(key))groups.set(key,[]);if(['frequency_hz','z_real_ohm','z_imag_ohm','cycle'].some(k=>r[k]===''||!Number.isFinite(Number(r[k]))))throw Error('Missing or non-finite numeric input');if(Number(r.frequency_hz)<=0)throw Error('Frequency must be positive');groups.get(key).push(r);}
    let j=0;for(const [name,records] of groups){if(j>=12)break;const a=records.sort((a,b)=>Number(b.frequency_hz)-Number(a.frequency_hz));if(new Set(a.map(r=>Number(r.frequency_hz))).size!==a.length)throw Error('Duplicate frequency in '+name);
      const f=a.map(r=>Number(r.frequency_hz)),re=a.map(r=>Number(r.z_real_ohm)),ni=a.map(r=>-Number(r.z_imag_ohm)),fit=fitCircuit(f,re,ni),id='uploaded-'+j++;
      const article=document.createElement('article');article.className='full';const h=document.createElement('h3');h.textContent=name;article.append(h);const div=document.createElement('div');div.id=id;div.className='chart';article.append(div);const p=document.createElement('p');p.className='small';p.textContent=t('Candidate weighted RMS residual ','候选加权 RMS 残差 ')+num(fit.weightedRmsPercent,3)+'%. '+t('No KK result, mechanism or future-capacity inference is claimed.','未计算 KK，不推断机制或未来容量。');article.append(p);$('importResults').append(article);
      chart(id,[{name:t('Measured','实测'),points:re.map((v,i)=>[v,ni[i]]),scatter:true},{name:'CPE',points:fit.real.map((v,i)=>[v,fit.negImag[i]])}],{xlabel:'Re(Z) / Ω',ylabel:'−Im(Z) / Ω',equal:true});}
    $('importStatus').textContent=t('Imported '+rows.length+' measured points in '+groups.size+' spectra. Showing up to 12 fits. File stays in memory.','已导入 '+rows.length+' 个实测点，'+groups.size+' 条谱，最多展示 12 条拟合。文件仅保留在浏览器内存。');
  }else{
    const analyses=cyclingAnalysis(rows);let j=0;for(const a of analyses){const article=document.createElement('article');article.className='full';const h=document.createElement('h3');h.textContent=a.id;article.append(h);const p=document.createElement('p');p.textContent=a.eligible?t('Eligible constant-current segment; capacity span ','通过恒流段检查；容量跨度 ')+num(a.capacitySpan,3)+' mAh':a.reason;article.append(p);if(a.eligible){const div=document.createElement('div');div.id='cycling-'+j++;div.className='chart';article.append(div);$('importResults').append(article);chart(div.id,[{name:'dQ/dV',points:a.dqdv}],{xlabel:'Voltage / V',ylabel:'dQ/dV / mAh V⁻¹'});}else $('importResults').append(article);}
    $('importStatus').textContent=t('Checked '+analyses.length+' segments. Eligible: '+analyses.filter(a=>a.eligible).length+'.','检查 '+analyses.length+' 个片段，符合条件 '+analyses.filter(a=>a.eligible).length+' 个。');
  }$('importStatus').className='success';
}catch(e){$('importStatus').textContent=e.message;$('importStatus').className='error';}};
const translations={
  '← Homepage':'← 返回主页','Prediction study':'预测研究','EIS workbench':'EIS 工作台','Your data':'你的数据','Tool library':'计算工具库','ELECTROCHEMICAL ANALYSIS':'电化学分析流程',
  'What runs here':'实际运行的内容','Real numerical analysis in your browser. Reference EIS calculations use impedance.py.':'浏览器执行真实数值分析；EIS 参考计算调用 impedance.py。',
  'This version uses a deterministic workflow. Live LLM planning and autonomous code revision are future capabilities.':'当前版本采用确定性流程。实时大模型规划与自主代码修订是后续能力。',
  'State-V EIS for discharge-capacity prediction':'基于 State-V EIS 的放电容量预测','Export report ↓':'导出研究报告 ↓','Prediction horizon':'预测范围','Test condition':'测试条件',
  'Run investigation ↗':'运行研发流程 ↗','5 diagnostic indices ahead':'预测后续 5 个诊断序号','10 diagnostic indices ahead':'预测后续 10 个诊断序号','20 diagnostic indices ahead':'预测后续 20 个诊断序号',
  '25°C · different cells':'25°C · 不同电池','35°C · temperature shift':'35°C · 温度迁移','45°C · temperature shift':'45°C · 温度迁移','HELD-OUT-CELL RESULTS':'留出电池的预测结果',
  'Measured and predicted capacity':'实测容量与预测容量','Prediction vs. withheld measurement':'预测与留出实测值','Cell':'电池','Model comparison':'预测模型比较','Development validation':'开发集验证',
  'Next experiment':'下一步实验','Execution record':'实际执行记录','Prediction CSV ↓':'导出预测 CSV ↓','Inspect model and numerical source':'查看模型及数值代码',
  'Cell-held-out prediction protocol':'按电池留出的容量预测方案','Equivalent-circuit fitting and linear-KK assessment':'等效电路拟合与线性 KK 一致性检查','Diagnostic index':'诊断序号','Fit candidate circuit ↗':'拟合候选电路 ↗',
  'Nyquist plot':'Nyquist 图','Modulus and frequency':'阻抗模值与频率','Checks before interpretation':'解释结果之前的检查','Battery-test CSV import and dQ/dV analysis':'电池测试 CSV 导入与 dQ/dV 分析',
  'Choose a CSV':'选择 CSV 文件','EIS CSV':'EIS 数据 CSV','Cycling CSV and dQ/dV':'充放电数据与 dQ/dV','Download measured EIS example ↓':'下载实测 EIS 示例 ↓','Download column template ↓':'下载列名模板 ↓',
  'Battery data-processing and calculation tools':'电池数据处理与计算工具','The agent\'s research job':'Agent 的研发任务',
  'Train on four cells at 25°C. Choose regularization using development cells only. Evaluate on different cells and compare with simply carrying today\'s capacity forward.':'使用 25°C 下的四个电池开发模型，仅用开发集选择正则化参数。在不同电池上检验，并与沿用当前容量的简单基线比较。',
  'The difficult work is deciding what data and assumptions each calculation needs, then checking whether its result answers the research question.':'真正繁琐的是判断每项计算需要什么数据、满足什么假设，并检查结果是否回答了研发问题。',
  'Files stay in this browser session. This version reads the CSV schemas below; vendor-native files need a verified adapter.':'文件保留在本次浏览器会话中。此版本支持以下 CSV 格式；仪器原生文件需要验证过的适配器。',
  'Inspect measured Nyquist curves and a model-dependent fit. Three reference spectra include linear-KK checks and RC-versus-CPE fits computed with impedance.py.':'查看实测 Nyquist 曲线及依赖模型的拟合。三个参考谱包含 impedance.py 计算的线性 KK 检查及 RC/CPE 电路比较。'
};
if(zh){document.documentElement.lang='zh-CN';const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let node;while(node=walker.nextNode()){const key=node.textContent.trim();if(translations[key])node.textContent=node.textContent.replace(key,translations[key]);}$('hero').innerHTML='电化学阻抗谱分析<br>与放电容量衰减预测';$('lead').textContent='关联 12 个 LCO/石墨电池的 State-V 阻抗谱与放电容量诊断，比较等效电路拟合，并检验跨电池、跨温度条件下的未来容量预测。';}
toolsTable();
try{const response=await fetch(new URL('./data.json',import.meta.url));if(!response.ok)throw Error('Dataset HTTP '+response.status);data=await response.json();
  if(data.schema!=='sciencesloop.battery-demo.v1')throw Error('Unsupported dataset schema');
  $('facts').innerHTML=[[data.audit.cells.length,t('independent cells','独立电池')],[data.samples.length,t('aligned EIS/capacity records','配对 EIS/容量记录')],[60,t('frequencies per spectrum','每条谱的频率点')],[3,t('temperatures: 25 / 35 / 45°C','温度：25 / 35 / 45°C')]].map(([a,b])=>'<div class="fact"><b>'+a+'</b><small>'+b+'</small></div>').join('');
  $('status').textContent=t('Measured data loaded · raw archive checksums verified · ready to compute','实测数据已载入 · 原始压缩包校验和已核对 · 可以开始计算');
  $('run').disabled=false;$('eisCell').innerHTML=data.audit.cells.map(c=>'<option '+(c==='25C05'?'selected':'')+'>'+esc(c)+'</option>').join('');populateCycles();
}catch(e){$('status').textContent=t('Data loading failed: ','数据加载失败：')+e.message;$('status').className='error';}
