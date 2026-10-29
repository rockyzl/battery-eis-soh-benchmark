import {circuit,fitCircuit} from '../analysis.mjs';
export function compareEisHypotheses(f,early,late){
 if(early.real.length!==f.length||late.real.length!==f.length||early.negImag.length!==f.length||late.negImag.length!==f.length)throw Error('Spectra must share the same frequency grid');
 const valid=f.map((v,i)=>i).filter(i=>vFinite(f[i])&&Number.isFinite(late.real[i])&&Number.isFinite(late.negImag[i])&&late.negImag[i]>=0);
 if(valid.length<10)throw Error('Insufficient comparable capacitive points');
 const baseline=fitCircuit(f,early.real,early.negImag),refit=fitCircuit(f,late.real,late.negImag);
 const weight=i=>1/Math.max(late.real[i]**2+late.negImag[i]**2,1e-12);
 const shifted=[...baseline.parameters];
 const shift=valid.reduce((s,i)=>s+weight(i)*(late.real[i]-circuit(baseline.parameters,f[i])[0]),0)/valid.reduce((s,i)=>s+weight(i),0);
 shifted[0]=Math.max(1e-8,baseline.parameters[0]+shift);
 const score=p=>100*Math.sqrt(valid.reduce((s,i)=>{const z=circuit(p,f[i]);return s+weight(i)*((z[0]-late.real[i])**2+(z[1]-late.negImag[i])**2);},0)/valid.length);
 const candidates=[{id:'unchanged',label:'Early fit transferred unchanged',freeParameters:0,weightedRmsPercent:score(baseline.parameters),parameters:baseline.parameters},{id:'r0_only',label:'Only R0 updated on the later spectrum',freeParameters:1,weightedRmsPercent:score(shifted),parameters:shifted},{id:'all_parameters',label:'All five CPE/Warburg parameters refitted',freeParameters:5,weightedRmsPercent:score(refit.parameters),parameters:refit.parameters}];
 return {cell:early.cell,referenceIndex:early.cycle,targetIndex:late.cycle,circuit:'R0-p(R1,CPE1)-W1',pointsScored:valid.length,baselineFitWeightedRmsPercent:baseline.weightedRmsPercent,candidates,r0EarlyOhm:baseline.parameters[0],r0OnlyUpdatedOhm:shifted[0],r0FullRefitOhm:refit.parameters[0],parameterNames:baseline.names,parameterUnits:baseline.units,method:'All candidates scored on identical later-spectrum capacitive points with the same modulus weights. R0-only update is the analytic constrained weighted least-squares real-axis shift; imaginary predictions remain unchanged.',limits:'Descriptive within-dataset fit comparison, not a held-out prediction or statistical significance test. More fitted parameters can reduce residuals; this does not establish a unique mechanism or prove aging. Conditions must be checked before physical attribution. The simple optimizer is not proof of a global optimum. Reference/target indices are diagnostic indices, not physical cycle counts.'};
}
function vFinite(v){return Number.isFinite(v)&&v>0;}
