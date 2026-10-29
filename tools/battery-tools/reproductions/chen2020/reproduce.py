"""Partial Chen et al. 2020 validation reproduction; never a new experiment.
Raw validation data: DOI 10.5281/zenodo.4032561 (CC BY 4.0).
Paper: DOI 10.1149/1945-7111/ab9050. See README for scoring limitations.
"""
import os
os.environ['PYBAMM_DISABLE_TELEMETRY']='true'
from pathlib import Path
import json,hashlib,time
import numpy as np
import pandas as pd
import pybamm
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'results';OUT.mkdir(exist_ok=True)
RATES=[(.5,12,1.3e-14),(1.,17,3.3e-14),(1.5,22,6.3e-14)]
meta=json.loads((ROOT/'zenodo_metadata.json').read_text())
expected={f['key']:f['checksum'].split(':')[1] for f in meta['files']}
raw={};manifest=[]
for f in sorted((ROOT/'raw').glob('*.csv')):
 b=f.read_bytes();assert hashlib.md5(b).hexdigest()==expected[f.name]
 d=pd.read_csv(f,skiprows=13);raw[f.stem]=d
 manifest.append(dict(file=f.name,rows=len(d),md5=hashlib.md5(b).hexdigest(),sha256=hashlib.sha256(b).hexdigest()))
results={'paper_doi':'10.1149/1945-7111/ab9050','data_doi':'10.5281/zenodo.4032561','pybamm_version':pybamm.__version__,'solver':'IDAKLUSolver default tolerances','model':'DFN isothermal Chen2020, 298.15 K','files':manifest,'runs':[],'metric_definition':'Each cell scored independently on a uniform 10 s grid over shared discharge duration; rest scored separately on shared 0..7200 s from each discharge endpoint. No discharge time scaling. Means are arithmetic across three cell RMSEs; not paper Figure 17 metrics.'}
fig,axes=plt.subplots(2,3,figsize=(13,7))
for j,(rate,step,dn) in enumerate(RATES):
 measured=[]
 for cell,d in raw.items():
  discharge=d[(d.Step==step)&(d.Md=='D')];rest=d[(d.Step==step+1)&(d.Md=='R')]
  assert len(discharge)>100 and len(rest)>200
  td=discharge['Test Time [s]'].to_numpy();td=td-td[0]
  tr=rest['Test Time [s]'].to_numpy()-float(discharge['Test Time [s]'].iloc[-1])
  measured.append(dict(cell=cell,td=td,vd=discharge['Voltage [V]'].to_numpy(),tr=tr,vr=rest['Voltage [V]'].to_numpy(),duration_s=float(td[-1]),capacity_ah=float(discharge['Capacity [Ah]'].iloc[-1]),mean_current_a=float(discharge['Current [A]'].mean()),mean_surface_c=float(discharge['Temperature Cell [degC]'].mean()),mean_chamber_c=float(discharge['Temperature Chamber [degC]'].mean())))
  axes[0,j].plot(td/3600,discharge['Voltage [V]'],color='black',alpha=.3,lw=1)
  axes[1,j].plot(tr/60,rest['Voltage [V]'],color='black',alpha=.3,lw=1)
 for mode,color in [('fixed_1C_parameters','#d67532'),('paper_rate_specific_Dn','#217b78')]:
  p=pybamm.ParameterValues('Chen2020')
  if mode=='paper_rate_specific_Dn':p.update({'Negative particle diffusivity [m2.s-1]':dn})
  exp=pybamm.Experiment([f'Discharge at {rate}C until 2.5 V','Rest for 2 hours'],period='10 seconds')
  start=time.monotonic();sim=pybamm.Simulation(pybamm.lithium_ion.DFN(),parameter_values=p,experiment=exp,solver=pybamm.IDAKLUSolver());sol=sim.solve();elapsed=time.monotonic()-start
  sd=sol.cycles[0].steps[0];sr=sol.cycles[1].steps[0]
  td=sd['Time [s]'].entries;vd=sd['Voltage [V]'].entries
  tr=sr['Time [s]'].entries-td[-1];vr=sr['Voltage [V]'].entries
  metrics=[]
  for m in measured:
   gd=np.arange(10,min(td[-1],m['duration_s']),10);gr=np.arange(10,min(tr[-1],m['tr'][-1]),10)
   ed=np.interp(gd,td,vd)-np.interp(gd,m['td'],m['vd']);er=np.interp(gr,tr,vr)-np.interp(gr,m['tr'],m['vr'])
   metrics.append({k:v for k,v in m.items() if k not in ['td','vd','tr','vr']}|dict(discharge_rmse_mv=float(np.sqrt(np.mean(ed**2))*1000),rest_phase_aligned_rmse_mv=float(np.sqrt(np.mean(er**2))*1000),cutoff_time_error_s=float(td[-1]-m['duration_s']),shared_discharge_s=float(gd[-1]),unscored_measured_discharge_tail_s=float(max(0,m['duration_s']-td[-1]))))
  run=dict(rate_c=rate,mode=mode,negative_diffusivity_m2_s=p['Negative particle diffusivity [m2.s-1]'],runtime_s=elapsed,discharge_duration_s=float(td[-1]),discharge_capacity_ah=float(rate*5*td[-1]/3600),mean_discharge_rmse_mv=float(np.mean([m['discharge_rmse_mv'] for m in metrics])),mean_rest_rmse_mv=float(np.mean([m['rest_phase_aligned_rmse_mv'] for m in metrics])),cells=metrics,curves=dict(discharge_t_s=td.tolist(),discharge_voltage_v=vd.tolist(),rest_t_s=tr.tolist(),rest_voltage_v=vr.tolist()))
  results['runs'].append(run)
  axes[0,j].plot(td/3600,vd,color=color,label=mode)
  axes[1,j].plot(tr/60,vr,color=color,label=mode)
  print(rate,mode,'RMSE mV',round(run['mean_discharge_rmse_mv'],2),round(run['mean_rest_rmse_mv'],2),'duration',round(td[-1],1),flush=True)
 # measured curves retained for independent inspection / display
 results.setdefault('measurements',[]).append(dict(rate_c=rate,cells=[{**m,'td':m['td'].tolist(),'vd':m['vd'].tolist(),'tr':m['tr'].tolist(),'vr':m['vr'].tolist()} for m in measured]))
 axes[0,j].set(title=f'{rate:g}C discharge',xlabel='Elapsed discharge / h',ylabel='Voltage / V')
 axes[1,j].set(title=f'{rate:g}C relaxation (phase aligned)',xlabel='Time since cutoff / min',ylabel='Voltage / V')
 for ax in axes[:,j]:ax.grid(alpha=.15)
axes[0,0].legend(fontsize=7);fig.suptitle('Chen2020 LG M50: three measured cells (black) vs actual PyBaMM DFN runs\nPartial validation reproduction; rate-specific tuning is not independent prediction',fontsize=12)
fig.tight_layout();fig.savefig(OUT/'validation.png',dpi=160);fig.savefig(OUT/'validation.svg')
(OUT/'results.json').write_text(json.dumps(results,indent=2))
