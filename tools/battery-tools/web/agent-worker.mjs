import {simulatePulse} from './agent-simulation.mjs';
import {fitCircuit, investigate} from '../analysis.mjs';
let data;
export async function executeTool(action,args={},providedData){
 if(action==='simulate_pulse')return simulatePulse(Object.fromEntries(Object.entries(args).filter(([k,v])=>['ocv_v','current_a','r0_ohm','r1_ohm','tau_s','duration_s'].includes(k)&&v!==null)));
 data=providedData||data||await fetch(new URL('./data.json',import.meta.url)).then(r=>{if(!r.ok)throw Error('Dataset HTTP '+r.status);return r.json();});
 if(action==='inspect_data')return {source:data.source,cells:data.audit.cells,pairedRecords:data.samples.length,audit:data.audit,availableSpectra:data.spectra.map(s=>({cell:s.cell,cycle:s.cycle})),limitations:['Public measured Cambridge/Newcastle data only.','Diagnostic indices are not physical cycle counts.','No literature search or hardware control.']};
 if(action==='fit_eis'){
  const cell=args.cell||'25C05',available=data.spectra.filter(s=>s.cell===cell),s=available.find(s=>s.cycle===args.cycle)||(!args.cycle?available[0]:null);
  if(!s)throw Error('Requested spectrum is unavailable. Choose a listed cell and diagnostic index.');
  const fit=fitCircuit(data.frequency_hz,s.real,s.negImag),ref=data.references.find(r=>r.cell===s.cell&&r.cycle===s.cycle);
  return {cell:s.cell,cycle:s.cycle,circuit:'R0-p(R1,CPE1)-W1',engine:'browser numerical optimizer',weightedRmsPercent:fit.weightedRmsPercent,fitPoints:fit.fitPoints,parameters:fit.names.map((name,i)=>({name,value:fit.parameters[i],unit:fit.units[i]})),linearKK:ref?{engine:'impedance.py 1.7.1 reference',rmsPercent:ref.kk.rms_percent,M:ref.kk.M}:null,referenceCandidates:ref?.models.map(m=>({circuit:m.circuit,rmsPercent:m.best.weighted_rms_percent,aic:m.best.aic,starts:m.successful_starts})),limits:'KK values are previously computed references, not a new browser KK calculation. Smaller fit error does not establish a unique physical mechanism.'};
 }
 if(action==='predict_capacity'){
  const r=investigate(data,{horizon:args.horizon||5,domain:args.domain||'25'});
  return {horizon:r.horizon,trainCells:r.trainCells,testCells:r.testCells,modelMAE:r.metrics.mae,persistenceMAE:r.persistence.mae,baselineMAE:r.baseline.mae,unit:'mAh',alpha:r.alpha,limits:r.limitations};
 }
 throw Error('Tool not permitted');
}
if(typeof self!=='undefined'&&typeof document==='undefined')self.onmessage=async({data:job})=>{try{self.postMessage({id:job.id,output:await executeTool(job.action,job.arguments)});}catch(e){self.postMessage({id:job.id,error:e.message});}};
