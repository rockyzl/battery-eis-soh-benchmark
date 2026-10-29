// Small, dependency-free numerical core. All predictions are computed, never scripted.
export const mean = a => a.reduce((s, x) => s + x, 0) / a.length;
export const quantile = (a, p) => { const b = [...a].sort((x,y)=>x-y); if (!b.length) return NaN; const k=(b.length-1)*p, i=Math.floor(k); return b[i]+(b[Math.ceil(k)]-b[i])*(k-i); };
export function solve(A, b) {
  const m = A.map((r,i)=>[...r,b[i]]), n=b.length;
  for (let k=0;k<n;k++) {
    let p=k; for(let i=k+1;i<n;i++) if(Math.abs(m[i][k])>Math.abs(m[p][k]))p=i;
    [m[p],m[k]]=[m[k],m[p]];
    if(Math.abs(m[k][k])<1e-14) throw Error('Singular system');
    const q=m[k][k]; for(let j=k;j<=n;j++)m[k][j]/=q;
    for(let i=0;i<n;i++) if(i!==k){const t=m[i][k];for(let j=k;j<=n;j++)m[i][j]-=t*m[k][j];}
  }
  return m.map(r=>r[n]);
}
export function ridge(rows, alpha) {
  if(rows.length<3)throw Error('At least three training records are required');
  const p=rows[0].features.length, xm=Array.from({length:p},(_,j)=>mean(rows.map(r=>r.features[j])));
  const sd=xm.map((m,j)=>Math.sqrt(mean(rows.map(r=>(r.features[j]-m)**2)))||1);
  const ym=mean(rows.map(r=>r.target));
  const A=Array.from({length:p},(_,j)=>Array.from({length:p},(_,k)=>j===k?alpha:0)),b=Array(p).fill(0);
  for(const r of rows){const x=r.features.map((v,j)=>(v-xm[j])/sd[j]);for(let j=0;j<p;j++){b[j]+=x[j]*(r.target-ym);for(let k=0;k<p;k++)A[j][k]+=x[j]*x[k];}}
  const beta=solve(A,b);
  return {alpha,xm,sd,ym,beta,predict:r=>ym+r.features.reduce((s,v,j)=>s+beta[j]*(v-xm[j])/sd[j],0)};
}
export function metrics(rows, key='prediction') {
  const cells=[...new Set(rows.map(r=>r.cell))];
  const perCell=cells.map(cell=>{const a=rows.filter(r=>r.cell===cell);return {cell,n:a.length,mae:mean(a.map(r=>Math.abs(r[key]-r.target))),rmse:Math.sqrt(mean(a.map(r=>(r[key]-r.target)**2)))};});
  return {n:rows.length,cells:cells.length,mae:mean(perCell.map(r=>r.mae)),rmse:mean(perCell.map(r=>r.rmse)),perCell};
}
export function makePairs(samples,horizon) {
  const lookup=new Map(samples.map(r=>[r.cell+':'+r.cycle,r]));
  return samples.flatMap(r=>{const future=lookup.get(r.cell+':'+(r.cycle+horizon));return future?[{...r,target:future.capacity,targetCycle:future.cycle}]:[];});
}
export function investigate(data,{horizon=5,domain='25',testCells}={}) {
  const trainCells=['25C01','25C02','25C03','25C04'];
  const pairs=makePairs(data.samples,horizon);
  const available=[...new Set(pairs.map(r=>r.cell))];
  const trainIds=trainCells.filter(c=>available.includes(c));
  let testIds=testCells??available.filter(c=>domain==='25'? /^25C0[5-8]$/.test(c):c.startsWith(domain+'C'));
  if(!trainIds.length){ // uploaded datasets: deterministic alphabetical whole-cell split
    const ids=available.sort();trainIds.push(...ids.slice(0,Math.max(2,Math.floor(ids.length*.67))));
    testIds=ids.filter(c=>!trainIds.includes(c));
  }
  if(testIds.some(c=>trainIds.includes(c)))throw Error('Training and test cells overlap');
  const train=pairs.filter(r=>trainIds.includes(r.cell)),test=pairs.filter(r=>testIds.includes(r.cell));
  if(trainIds.length<3||!test.length)throw Error('Need at least three development cells and one separate test cell with future labels');
  const alphas=[.1,1,10,100];
  const candidates=alphas.map(alpha=>{
    const oof=trainIds.flatMap(cell=>{const a=train.filter(r=>r.cell!==cell),b=train.filter(r=>r.cell===cell);const m=ridge(a,alpha);return b.map(r=>({...r,prediction:m.predict(r)}));});
    return {alpha,cv:metrics(oof),oof};
  });
  const selected=candidates.reduce((a,b)=>a.cv.mae<=b.cv.mae?a:b);
  const model=ridge(train,selected.alpha),mu=mean(train.map(r=>r.target));
  const radius=quantile(selected.oof.map(r=>Math.abs(r.prediction-r.target)),.9);
  const predictions=test.map(r=>({...r,prediction:model.predict(r),baseline:mu,persistence:r.capacity,lower:model.predict(r)-radius,upper:model.predict(r)+radius}));
  const outliers=predictions.filter(r=>r.features.some((v,j)=>Math.abs((v-model.xm[j])/model.sd[j])>5)).length;
  const correlations=model.xm.map((_,j)=>{
    const xs=train.map(r=>r.features[j]),ys=train.map(r=>r.target),x=mean(xs),y=mean(ys);
    const cov=xs.reduce((s,v,i)=>s+(v-x)*(ys[i]-y),0),xx=xs.reduce((s,v)=>s+(v-x)**2,0),yy=ys.reduce((s,v)=>s+(v-y)**2,0);
    return {feature:j,r:cov/Math.sqrt(xx*yy)||0};
  }).sort((a,b)=>Math.abs(b.r)-Math.abs(a.r));
  const result={horizon,domain,trainCells:trainIds,testCells:testIds,trainN:train.length,testN:test.length,
    droppedPairs:data.samples.length-pairs.length,cv:candidates.map(x=>({alpha:x.alpha,mae:x.cv.mae})),
    alpha:selected.alpha,cvMae:selected.cv.mae,metrics:metrics(predictions),baseline:metrics(predictions,'baseline'),persistence:metrics(predictions,'persistence'),
    radius,coverage:mean(predictions.map(r=>r.target>=r.lower&&r.target<=r.upper?1:0)),outliers,correlations,predictions,
    model:{alpha:model.alpha,xm:model.xm,sd:model.sd,ym:model.ym,beta:model.beta},
    limitations:['Exploratory public-data demonstration; test outcomes are visible and this is not a sealed external validation.',
      'Repeated spectra within a cell are correlated; independent units are cells, and metrics average over cells.',
      'Residual bands are exploratory, calibrated on development-cell out-of-fold residuals, without a coverage guarantee.',
      'Prediction and correlation do not identify a degradation mechanism. No live LLM or instrument control runs here.']};
  return result;
}

// Deterministic weighted least-squares circuit fit for browser imports.
// R0 + parallel(R1,CPE) + semi-infinite Warburg. Neg-imag convention is explicit.
export function circuit(parameters,f) {
  const [r0,r1,q,n,w]=parameters,omega=2*Math.PI*f;
  const a=1/r1+q*omega**n*Math.cos(n*Math.PI/2),b=q*omega**n*Math.sin(n*Math.PI/2),den=a*a+b*b;
  return [r0+a/den+w/Math.sqrt(omega),b/den+w/Math.sqrt(omega)];
}
export function fitCircuit(f,real,negImag) {
  const valid=f.map((v,i)=>i).filter(i=>f[i]>0&&Number.isFinite(real[i])&&negImag[i]>=0);
  if(valid.length<10)throw Error('Need at least ten finite capacitive points for this candidate circuit');
  const decode=x=>[Math.exp(x[0]),Math.exp(x[1]),Math.exp(x[2]),1/(1+Math.exp(-x[3])),Math.exp(x[4])];
  const objective=x=>{
    if(x.some(v=>Math.abs(v)>30))return 1e30;
    const p=decode(x);return valid.reduce((s,i)=>{const z=circuit(p,f[i]),d=real[i]**2+negImag[i]**2;return s+((z[0]-real[i])**2+(z[1]-negImag[i])**2)/Math.max(d,1e-12);},0);
  };
  const r0=Math.max(.00001,Math.min(...real)),r1=Math.max(.001,Math.max(...real)-r0);
  const start=[Math.log(r0),Math.log(r1),Math.log(.03),1.4,Math.log(.2)];
  let simplex=[start,...start.map((_,j)=>start.map((x,k)=>x+(j===k?.35:0)))];
  for(let it=0;it<650;it++) {
    simplex.sort((a,b)=>objective(a)-objective(b));const best=simplex[0],worst=simplex[5];
    if(it>50&&Math.abs(objective(worst)-objective(best))<1e-12)break;
    const c=best.map((_,j)=>mean(simplex.slice(0,5).map(x=>x[j]))),r=c.map((v,j)=>v+(v-worst[j]));
    if(objective(r)<objective(best)){const e=c.map((v,j)=>v+2*(r[j]-v));simplex[5]=objective(e)<objective(r)?e:r;}
    else if(objective(r)<objective(simplex[4]))simplex[5]=r;
    else {const a=objective(r)<objective(worst)?r:worst,candidate=c.map((v,j)=>v+.5*(a[j]-v));
      if(objective(candidate)<Math.min(objective(worst),objective(r)))simplex[5]=candidate;
      else simplex=simplex.map((x,i)=>i===0?x:x.map((v,j)=>best[j]+.5*(v-best[j])));
    }
  }
  simplex.sort((a,b)=>objective(a)-objective(b));const p=decode(simplex[0]);
  return {parameters:p,names:['R0','R1','Q','n','W'],units:['Ohm','Ohm','Ohm^-1 s^n','dimensionless','Ohm s^-1/2'],
    weightedRmsPercent:100*Math.sqrt(objective(simplex[0])/valid.length),fitPoints:valid.length,
    real:f.map(v=>circuit(p,v)[0]),negImag:f.map(v=>circuit(p,v)[1])};
}

export function parseCSV(text) {
  const lines=text.replace(/^\uFEFF/,'').trim().split(/\r?\n/).filter(l=>l.trim());
  const split=line=>{const out=[];let value='',quoted=false;for(let i=0;i<line.length;i++){const c=line[i];if(c==='"'){if(quoted&&line[i+1]==='"'){value+='"';i++;}else quoted=!quoted;}else if(c===','&&!quoted){out.push(value.trim());value='';}else value+=c;}if(quoted)throw Error('Unclosed CSV quote');out.push(value.trim());return out;};
  const names=split(lines[0]).map(x=>x.toLowerCase());if(new Set(names).size!==names.length)throw Error('Duplicate CSV column name');
  return lines.slice(1).map((l,i)=>{const a=split(l);if(a.length!==names.length)throw Error('CSV column count differs at row '+(i+2));return Object.fromEntries(names.map((n,j)=>[n,a[j]]));});
}
export function cyclingAnalysis(rows) {
  const required=['cell_id','cycle','voltage_v','capacity_mah','current_a'];
  if(!rows.length||required.some(k=>!(k in rows[0])))throw Error('Cycling CSV requires '+required.join(', '));
  const groups=new Map();
  for(const r of rows){const keys=['cycle','voltage_v','capacity_mah','current_a'];if(keys.some(k=>r[k]===''||r[k]==null))throw Error('Missing cycling value');const values=keys.map(k=>Number(r[k]));if(values.some(x=>!Number.isFinite(x)))throw Error('Non-finite cycling value');const sign=Math.sign(values[3]);if(!sign)continue;const key=r.cell_id+':'+r.cycle+':'+sign;if(!groups.has(key))groups.set(key,[]);groups.get(key).push({v:values[1],q:values[2],i:values[3]});}
  return [...groups].map(([id,a])=>{
    const absI=a.map(x=>Math.abs(x.i)),cv=Math.sqrt(mean(absI.map(x=>(x-mean(absI))**2)))/mean(absI);
    if(cv>.05)return {id,eligible:false,reason:'Current varies by more than 5%; split constant-current steps first',n:a.length};
    const direction=Math.sign(a.at(-1).v-a[0].v);const bad=a.slice(1).filter((x,i)=>(x.v-a[i].v)*direction<-.002).length;
    if(bad>a.length*.02)return {id,eligible:false,reason:'Voltage direction changes; separate protocol steps',n:a.length};
    const b=[...a].sort((x,y)=>x.v-y.v),lo=b[0].v,hi=b.at(-1).v;if(hi-lo<.05||b.length<20)return {id,eligible:false,reason:'Need at least 20 points and 50 mV span',n:a.length};
    const grid=[];for(let v=lo+.01;v<hi-.01;v+=.01){const left=b.filter(x=>x.v>=v-.01&&x.v<=v+.01);if(left.length<3)continue;const xm=mean(left.map(x=>x.v)),ym=mean(left.map(x=>x.q)),den=left.reduce((s,x)=>s+(x.v-xm)**2,0);if(den>1e-12)grid.push([v,left.reduce((s,x)=>s+(x.v-xm)*(x.q-ym),0)/den]);}
    return {id,eligible:true,n:a.length,capacitySpan:Math.abs(a.at(-1).q-a[0].q),currentCV:cv,dqdv:grid,windowV:.02};
  });
}
