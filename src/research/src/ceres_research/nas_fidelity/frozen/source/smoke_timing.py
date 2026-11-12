"""Non-scoring cost calibration. Synthetic tensors only; no CIFAR or scores read."""
import platform,sys,time
import torch
from torch import nn
from core import ROOT,setup,space,aid,TinyCNN,save,stamp
setup();torch.manual_seed(880001)
x=torch.rand(128,3,32,32);y=torch.arange(128)%10;rows=[]
for a in space():
    torch.manual_seed(880001);t,c=time.perf_counter(),time.process_time()
    model=TinyCNN(a);opt=torch.optim.Adam(model.parameters(),lr=.003,weight_decay=.0001)
    build_wall,build_cpu=time.perf_counter()-t,time.process_time()-c
    batches=[]
    for j in range(10):
        t,c=time.perf_counter(),time.process_time();opt.zero_grad(set_to_none=True)
        loss=nn.functional.cross_entropy(model(x),y);loss.backward();opt.step()
        batches.append({"wall":time.perf_counter()-t,"cpu":time.process_time()-c})
    model.eval();t,c=time.perf_counter(),time.process_time()
    with torch.no_grad():
        for _ in range(8): model(x)
    rows.append({"architecture":a,"architecture_id":aid(a),"build_wall_seconds":build_wall,"build_cpu_seconds":build_cpu,
       "warmup_batches":batches[:2],"timed_batches":batches[2:],
       "projected_epoch32_batches_wall":sum(b["wall"] for b in batches[2:])*4,
       "projected_epoch32_batches_cpu":sum(b["cpu"] for b in batches[2:])*4,
       "validation8_batches_wall":time.perf_counter()-t,"validation8_batches_cpu":time.process_time()-c})
epoch_sum=sum(r["projected_epoch32_batches_wall"] for r in rows)
val_sum=sum(r["validation8_batches_wall"] for r in rows)
save(ROOT/"artifacts/smoke_timing.json",{"at_utc":stamp(),"synthetic_only":True,"scores_recorded":False,"cifar_data_read":False,
 "torch":torch.__version__,"python":sys.version,"platform":platform.platform(),"threads":1,"rows":rows,
 "projection_8_arch_10_seeds_20epochs_plus_each_epoch_val_seconds":10*20*(epoch_sum+val_sum),
 "projection_8_arch_20_seeds_20epochs_plus_each_epoch_val_seconds":20*20*(epoch_sum+val_sum),
 "projection_excludes":"serialization, data loading, Python and audit overhead; not a performance measurement"})
print((ROOT/"artifacts/smoke_timing.json").read_text())
