import assert from 'node:assert/strict';
import {ridge,investigate,circuit,fitCircuit,cyclingAnalysis,parseCSV,makePairs} from './analysis.mjs';
// A known linear relation must survive standardization and recover predictions.
const rows=Array.from({length:40},(_,i)=>({features:[i,Math.sin(i)],target:3*i+2}));
assert.ok(Math.abs(ridge(rows,1e-8).predict({features:[45,Math.sin(45)]})-137)<1e-6);
// Future targets are exact same-cell joins, never the next arbitrary row.
assert.equal(makePairs([{cell:'a',cycle:1,capacity:2},{cell:'a',cycle:3,capacity:1}],1).length,0);
// Held-out label perturbations cannot affect model choice or coefficients.
const samples=[];
for(let c=1;c<=8;c++)for(let k=1;k<=20;k++)samples.push({cell:'25C0'+c,cycle:k,capacity:40-c*.2-k*.1,q0:40,features:[k,c,k*.02,c*k*.01,1,2,3,4,5,6]});
const a=investigate({samples}),changed=samples.map(r=>r.cell>='25C05'?{...r,capacity:r.capacity*1.5}:r),b=investigate({samples:changed});
assert.equal(a.alpha,b.alpha);assert.deepEqual(a.model,b.model);assert.notEqual(a.metrics.mae,b.metrics.mae);
// Fit a known circuit: recovery of the curve is the identifiable target here.
const f=Array.from({length:50},(_,i)=>10**(4-i*.12)),p=[.3,.8,.02,.75,.2],z=f.map(v=>circuit(p,v));
const fit=fitCircuit(f,z.map(x=>x[0]),z.map(x=>x[1]));assert.ok(fit.weightedRmsPercent<.1);
// Local-linear differentiation recovers a known slope in constant current data.
const cycling=Array.from({length:100},(_,i)=>({cell_id:'a',cycle:'1',voltage_v:String(3+i*.005),capacity_mah:String(i*.5),current_a:'.01'}));
const d=cyclingAnalysis(cycling)[0];assert.equal(d.eligible,true);assert.ok(d.dqdv.every(x=>Math.abs(x[1]-100)<1e-7));
assert.equal(cyclingAnalysis(cycling.map((r,i)=>({...r,current_a:i<50?'.01':'.02'})))[0].eligible,false);
assert.throws(()=>cyclingAnalysis([{...cycling[0],voltage_v:''}]),/Missing/);
assert.throws(()=>parseCSV('a,a\n1,2'),/Duplicate/);
assert.equal(parseCSV('a,b\n"cell,A",2')[0].a,'cell,A');
console.log('PASS: linear recovery, exact future pairing, held-out leakage control, circuit recovery, dQ/dV and input checks');
