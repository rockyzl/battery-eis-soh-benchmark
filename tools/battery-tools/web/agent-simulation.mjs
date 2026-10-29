// Analytic one-RC Thevenin pulse response, with fixed OCV and no SOC/thermal dynamics.
export function simulatePulse({ocv_v=4,current_a=.01,r0_ohm=.35,r1_ohm=.6,tau_s=10,duration_s=10}={}){
 const values={ocv_v,current_a,r0_ohm,r1_ohm,tau_s,duration_s};
 if(Object.values(values).some(x=>!Number.isFinite(x)))throw Error('Simulation parameters must be finite');
 if(ocv_v<2.5||ocv_v>4.5||Math.abs(current_a)>.1||r0_ohm<=0||r0_ohm>20||r1_ohm<=0||r1_ohm>20||tau_s<.1||tau_s>2000||duration_s<1||duration_s>300)throw Error('Parameters exceed the demo simulation bounds');
 const polarization=current_a*r1_ohm*(1-Math.exp(-duration_s/tau_s));
 const samples=[];
 for(let i=0;i<=60;i++){const time=duration_s*i/60;samples.push({time_s:time,current_a,voltage_v:ocv_v-current_a*r0_ohm-current_a*r1_ohm*(1-Math.exp(-time/tau_s))});}
 for(let i=0;i<=60;i++){const rest=duration_s*i/60;samples.push({time_s:duration_s+rest,current_a:0,voltage_v:ocv_v-polarization*Math.exp(-rest/tau_s)});}
 return {model:'Thevenin R0 + one parallel RC, fixed OCV',parameters:values,parameterStatus:'User-editable illustrative parameters; not identified from this measured cell or from CPE fits.',instantaneousDropV:current_a*r0_ohm,endPulseVoltageV:ocv_v-current_a*r0_ohm-polarization,endRestVoltageV:samples.at(-1).voltage_v,polarizationAtPulseEndV:polarization,samples,limits:'Analytic forward simulation only. Fixed OCV, no SOC, thermal, diffusion or degradation dynamics; not PyBaMM. Does not establish measured-cell parameters.'};
}
