import {parseCSV,cyclingAnalysis} from '../analysis.mjs';
export function prepareLocalCSV(text){
 if(text.length>2000000)throw Error('This CSV adapter accepts files up to 2 MB. Select a representative export.');
 const rows=parseCSV(text);if(!rows.length||rows.length>25000)throw Error('Need 1–25,000 data rows');
 const has=cols=>cols.every(k=>Object.hasOwn(rows[0],k));
 const kind=has(['cell_id','cycle','frequency_hz','z_real_ohm','z_imag_ohm'])?'local_eis':has(['cell_id','cycle','voltage_v','capacity_mah','current_a'])?'local_cycling':null;
 if(!kind)throw Error('Use EIS columns cell_id,cycle,frequency_hz,z_real_ohm,z_imag_ohm or cycling columns cell_id,cycle,voltage_v,capacity_mah,current_a. Vendor-native formats require a verified export adapter.');
 const number=(r,k)=>{if(r[k]===''||r[k]==null||!Number.isFinite(Number(r[k])))throw Error('Missing or non-finite '+k);return Number(r[k]);};
 const groups=new Map();
 for(const r of rows){if(!r.cell_id?.trim())throw Error('cell_id must be present');const cycle=number(r,'cycle');if(!Number.isSafeInteger(cycle)||cycle<0)throw Error('cycle must be a nonnegative integer record index');const key=r.cell_id+'\u0000'+cycle;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
 if(groups.size>100)throw Error('Select at most 100 cell/index groups for this demo');
 const source={kind,title:'Researcher-provided local CSV',rawRowsStayInBrowser:true};
 if(kind==='local_eis'){
  const spectra=[...groups].map(([key,a])=>{const seen=new Set();const b=a.map(r=>{const f=number(r,'frequency_hz');if(f<=0||seen.has(f))throw Error('EIS frequencies must be positive and unique within each cell/index');seen.add(f);return [f,number(r,'z_real_ohm'),-number(r,'z_imag_ohm')];}).sort((a,b)=>b[0]-a[0]);if(b.length<10)throw Error('Each EIS spectrum needs at least ten frequency points');return {cell:a[0].cell_id,cycle:Number(a[0].cycle),frequency_hz:b.map(x=>x[0]),real:b.map(x=>x[1]),negImag:b.map(x=>x[2])};});
  return {kind,source,audit:{cells:[...new Set(spectra.map(s=>s.cell))],rowCount:rows.length,groupCount:spectra.length},samples:[],spectra,references:[]};
 }
 for(const r of rows)for(const k of ['voltage_v','capacity_mah','current_a'])number(r,k);
 return {kind,source,audit:{cells:[...new Set(rows.map(r=>r.cell_id))],rowCount:rows.length,groupCount:groups.size},samples:[],spectra:[],references:[],cyclingRows:rows,cyclingGroups:[...groups.values()].map(a=>({cell:a[0].cell_id,cycle:Number(a[0].cycle)}))};
}
export function summarizeDQDV(data,args={}){
 if(data.kind!=='local_cycling')throw Error('dQ/dV requires a selected local cycling CSV');
 const selected=data.cyclingGroups.find(g=>(!args.cell||g.cell===args.cell)&&(args.cycle==null||g.cycle===args.cycle));if(!selected)throw Error('Requested cycling record unavailable');
 const rows=data.cyclingRows.filter(r=>r.cell_id===selected.cell&&Number(r.cycle)===selected.cycle);
 const groups=cyclingAnalysis(rows).map(g=>{const base={id:g.id,eligible:g.eligible,n:g.n,reason:g.reason};if(!g.eligible)return base;const values=g.dqdv.map(p=>p[1]);if(!values.length)return {...base,eligible:false,reason:'No supported local-linear derivative windows'};let lo=0,hi=0;values.forEach((v,i)=>{if(v<values[lo])lo=i;if(v>values[hi])hi=i;});return {...base,currentCV:g.currentCV,capacitySpanMah:g.capacitySpan,windowV:g.windowV,derivativePointCount:g.dqdv.length,minimum:g.dqdv[lo],maximum:g.dqdv[hi],curve:g.dqdv.filter((_,i)=>i%Math.max(1,Math.ceil(g.dqdv.length/80))===0)};});
 return {cell:selected.cell,cycle:selected.cycle,groups,units:{voltage:'V',capacity:'mAh',dqdv:'mAh/V'},limits:'20 mV local-linear differentiation. Current variation and voltage reversals are checked; split CC/CV/rest protocol segments first. Extremes depend on preprocessing and are not identified phase-transition peaks. No diffusion coefficient, full impedance spectrum or unique degradation mechanism is inferred.'};
}
