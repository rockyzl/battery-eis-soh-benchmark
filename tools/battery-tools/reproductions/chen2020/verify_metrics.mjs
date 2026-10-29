import fs from 'node:fs';
import {validationTool} from '../../agent/agent-validation.mjs';
const d=JSON.parse(fs.readFileSync(new URL('./results/results.json',import.meta.url)));
for(const [i,r] of validationTool('compare_validation',{},d).comparisons.entries()){
 if(Math.abs(r.discharge_rmse_mv-d.runs[i].mean_discharge_rmse_mv)>1e-8||Math.abs(r.rest_rmse_mv-d.runs[i].mean_rest_rmse_mv)>1e-8)throw Error('Python/JS disagreement');
}
if(validationTool('compare_validation',{current_a:7.5},d).comparisons.length!==2)throw Error('Rate selection failed');
let rejected=false;try{validationTool('compare_validation',{current_a:6},d);}catch{rejected=true;}if(!rejected)throw Error('Unsupported rate accepted');
console.log('Metric equivalence and rate-selection checks passed');
