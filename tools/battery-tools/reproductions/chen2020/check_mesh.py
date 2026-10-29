import os
os.environ['PYBAMM_DISABLE_TELEMETRY']='true'
import pybamm,json,numpy as np
from pathlib import Path
root=Path(__file__).resolve().parent
old=json.loads((root/'results/results.json').read_text());out=[]
for rate,dn in [(.5,3.3e-14),(.5,1.3e-14),(1.5,3.3e-14),(1.5,6.3e-14)]:
 p=pybamm.ParameterValues('Chen2020');p.update({'Negative particle diffusivity [m2.s-1]':dn})
 e=pybamm.Experiment([f'Discharge at {rate}C until 2.5 V','Rest for 2 hours'],period='10 seconds')
 s=pybamm.Simulation(pybamm.lithium_ion.DFN(),parameter_values=p,experiment=e,var_pts={'x_n':40,'x_s':40,'x_p':40,'r_n':40,'r_p':40}).solve()
 ref=next(r for r in old['runs'] if r['rate_c']==rate and r['negative_diffusivity_m2_s']==dn)
 rest=s.cycles[1].steps[0];tr=rest['Time [s]'].entries-s.cycles[0].steps[0]['Time [s]'].entries[-1];vr=rest['Voltage [V]'].entries
 t=np.arange(10,7200,10);diff=np.interp(t,tr,vr)-np.interp(t,ref['curves']['rest_t_s'],ref['curves']['rest_voltage_v'])
 out.append(dict(rate_c=rate,dn=dn,rest_default_vs_refined_rmse_mv=float(np.sqrt(np.mean(diff**2))*1000),cutoff_shift_s=float(s.cycles[0].steps[0]['Time [s]'].entries[-1]-ref['discharge_duration_s'])))
(root/'results/mesh-check.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
