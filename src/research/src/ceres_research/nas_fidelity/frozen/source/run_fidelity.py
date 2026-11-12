"""Pre-frozen P4 fidelity diagnostic; every attempt and every epoch is journaled."""
import argparse,json,os,platform,resource,sys,time,traceback
import numpy as np
import torch
from torch import nn
from core import ROOT,setup,space,aid,TinyCNN,load_data,evaluate,macs,save,append,digest,stamp

def verify_freeze():
    frozen=json.loads((ROOT/"artifacts/pre_run_freeze.json").read_text())
    for name,h in frozen["files"].items():
        if digest(ROOT/name)!=h: raise ValueError(f"Frozen input changed: {name}")
    return frozen

def run_one(a,seed,group,tensors,cfg,freeze_hash,resume=False):
    name=f"{group}_{seed}_{aid(a)}";out=ROOT/"raw"/(name+".json")
    if out.exists():
        if resume:
            old=json.loads(out.read_text())
            assert old["freeze_sha256"]==freeze_hash
            assert len(old["epochs"])==cfg["high_epochs"]
            for cp in old["checkpoints"]: assert digest(ROOT/cp["path"])==cp["sha256"]
            return
        raise FileExistsError("No overwrite permitted: "+name)
    resume_path=ROOT/"checkpoints"/(name+"_resume.pt")
    journal=ROOT/"raw/attempts.jsonl"
    history=[json.loads(s) for s in journal.read_text().splitlines()] if journal.exists() else []
    prior_starts=sum(r.get("run_id")==name and r.get("status")=="started" for r in history)
    if prior_starts and not resume: raise ValueError("Previous attempt exists; explicit --resume required")
    t0,c0=time.perf_counter(),time.process_time()
    identity={"run_id":name,"group":group,"seed":seed,"architecture":a,"architecture_id":aid(a),"freeze_sha256":freeze_hash}
    append(ROOT/"raw/attempts.jsonl",dict(identity,status="started",at_utc=stamp()))
    try:
        torch.manual_seed(seed);model=TinyCNN(a)
        opt=torch.optim.Adam(model.parameters(),lr=cfg["lr"],weight_decay=cfg["weight_decay"])
        order_rng=torch.Generator().manual_seed(seed+10000000)
        build_wall,build_cpu=time.perf_counter()-t0,time.process_time()-c0
        x,y=tensors["train"];epochs=[];checkpoints=[];resume_count=prior_starts
        if resume_path.exists():
            if not resume: raise ValueError("Checkpoint exists; explicit --resume required")
            state=torch.load(resume_path,map_location="cpu",weights_only=True)
            assert state["freeze_sha256"]==freeze_hash and state["run_id"]==name
            model.load_state_dict(state["model_state_dict"]);opt.load_state_dict(state["optimizer_state_dict"])
            order_rng.set_state(state["order_rng_state"]);epochs=state["epochs"];checkpoints=state["checkpoints"]
            epoch_journal=ROOT/"raw/epochs.jsonl"
            existing=[json.loads(s) for s in epoch_journal.read_text().splitlines() if json.loads(s)["run_id"]==name] if epoch_journal.exists() else []
            if existing==epochs[:-1]: append(epoch_journal,epochs[-1])
            elif existing!=epochs: raise ValueError("Journal/checkpoint content mismatch")
            append(ROOT/"raw/attempts.jsonl",dict(identity,status="resumed",at_utc=stamp(),from_completed_epoch=len(epochs),
                previous_partial_epoch_cost="unknown; completed epoch costs retained; not counted as zero"))
        elif prior_starts:
            append(ROOT/"raw/attempts.jsonl",dict(identity,status="restarted_without_completed_epoch",at_utc=stamp(),from_completed_epoch=0,
                previous_partial_epoch_cost="unknown or failed-event logged; full aggregate timing is marked incomplete"))
        for epoch in range(len(epochs)+1,cfg["high_epochs"]+1):
            t,c=time.perf_counter(),time.process_time();order=torch.randperm(len(y),generator=order_rng)
            model.train();loss_sum=0.;correct=0
            for i in range(0,len(y),cfg["batch_size"]):
                ix=order[i:i+cfg["batch_size"]];opt.zero_grad(set_to_none=True)
                logits=model(x[ix]);loss=nn.functional.cross_entropy(logits,y[ix]);loss.backward();opt.step()
                loss_sum+=float(loss.detach())*len(ix);correct+=int((logits.argmax(1)==y[ix]).sum())
            train_wall,train_cpu=time.perf_counter()-t,time.process_time()-c
            t,c=time.perf_counter(),time.process_time();val,pred=evaluate(model,tensors["validation"],cfg["batch_size"])
            eval_wall,eval_cpu=time.perf_counter()-t,time.process_time()-c
            row=dict(identity,epoch=epoch,online_train_loss=loss_sum/len(y),online_train_correct=correct,online_train_n=len(y),
                validation=val,train_wall_seconds=train_wall,train_cpu_seconds=train_cpu,validation_wall_seconds=eval_wall,
                validation_cpu_seconds=eval_cpu,optimizer_steps=32,train_presentations=len(y),validation_presentations=val["n"],at_utc=stamp())
            if not np.isfinite(row["online_train_loss"]) or not np.isfinite(val["loss"]): raise ValueError("Nonfinite metric")
            epochs.append(row)
            if epoch in [cfg["low_epochs"],cfg["high_epochs"]]:
                path=ROOT/"checkpoints"/f"{name}_e{epoch:02d}.pt"
                torch.save(dict(identity,epoch=epoch,model_state_dict=model.state_dict(),optimizer_state_dict=opt.state_dict()),path)
                predpath=ROOT/"raw"/f"{name}_e{epoch:02d}_predictions.json"
                save(predpath,{"run_id":name,"epoch":epoch,"split_manifest_sha256":cfg["split_sha256"],"predictions":pred})
                checkpoints.append({"epoch":epoch,"path":str(path.relative_to(ROOT)),"sha256":digest(path),
                    "predictions_path":str(predpath.relative_to(ROOT)),"predictions_sha256":digest(predpath)})
            temp=resume_path.with_suffix(".tmp")
            torch.save(dict(identity,model_state_dict=model.state_dict(),optimizer_state_dict=opt.state_dict(),
                order_rng_state=order_rng.get_state(),epochs=epochs,checkpoints=checkpoints,resume_count=resume_count),temp)
            temp.replace(resume_path)
            append(ROOT/"raw/epochs.jsonl",row)
        result=dict(identity,epochs=epochs,checkpoints=checkpoints,build_wall_seconds=build_wall,build_cpu_seconds=build_cpu,
            parameters=sum(p.numel() for p in model.parameters()),forward_conv_linear_macs_per_image=macs(a),
            total_wall_seconds=time.perf_counter()-t0,total_cpu_seconds=time.process_time()-c0,completed_at_utc=stamp(),
            resumed_attempt_count=resume_count,timing_complete=(resume_count==0))
        save(out,result)
        append(ROOT/"raw/attempts.jsonl",dict(identity,status="completed",at_utc=stamp(),wall_seconds=result["total_wall_seconds"],cpu_seconds=result["total_cpu_seconds"]))
        print(f"COMPLETED {name} e2={epochs[cfg['low_epochs']-1]['validation']['accuracy']:.3f} e20={epochs[-1]['validation']['accuracy']:.3f} wall={result['total_wall_seconds']:.2f}",flush=True)
    except BaseException as e:
        append(ROOT/"raw/attempts.jsonl",dict(identity,status="failed",at_utc=stamp(),error=repr(e),traceback=traceback.format_exc(),wall_seconds=time.perf_counter()-t0,cpu_seconds=time.process_time()-c0))
        raise

def freeze_selections(cfg):
    output=ROOT/"artifacts/selection_freeze.json"
    if output.exists(): raise FileExistsError("Selection already exists")
    rows=[]
    for a in space():
        runs=[json.loads((ROOT/"raw"/f"discovery_{s}_{aid(a)}.json").read_text()) for s in cfg["discovery_seeds"]]
        rows.append({"architecture_id":aid(a),"architecture":a,
          "low_correct_sum":sum(r["epochs"][cfg["low_epochs"]-1]["validation"]["correct"] for r in runs),
          "high_correct_sum":sum(r["epochs"][-1]["validation"]["correct"] for r in runs)})
    pick=lambda k: sorted(rows,key=lambda r:(-r[k],r["architecture_id"]))[0]["architecture_id"]
    save(output,{"at_utc":stamp(),"rule":"highest summed endpoint validation correct counts across all five discovery seeds; lexicographic architecture_id breaks ties",
        "low_selected":pick("low_correct_sum"),"high_selected":pick("high_correct_sum"),"rows":rows,
        "independent_retraining_started":False,"official_test_used":False,
        "discovery_result_hashes":{str(p.relative_to(ROOT)):digest(p) for p in sorted((ROOT/"raw").glob("discovery_*.json"))}})
    save(ROOT/"artifacts/selection_freeze_receipt.json",{"at_utc":stamp(),"selection_sha256":digest(output),"pre_run_freeze_sha256":digest(ROOT/"artifacts/pre_run_freeze.json")})

def verify_selection(cfg):
    receipt=json.loads((ROOT/"artifacts/selection_freeze_receipt.json").read_text())
    assert receipt["selection_sha256"]==digest(ROOT/"artifacts/selection_freeze.json")
    assert receipt["pre_run_freeze_sha256"]==digest(ROOT/"artifacts/pre_run_freeze.json")
    frozen=json.loads((ROOT/"artifacts/selection_freeze.json").read_text())
    for path,h in frozen["discovery_result_hashes"].items(): assert digest(ROOT/path)==h
    rows=[]
    for a in space():
        vals=[json.loads((ROOT/"raw"/f"discovery_{s}_{aid(a)}.json").read_text()) for s in cfg["discovery_seeds"]]
        assert all(len(r["epochs"])==cfg["high_epochs"] for r in vals)
        rows.append({"architecture_id":aid(a),"architecture":a,"low_correct_sum":sum(r["epochs"][cfg["low_epochs"]-1]["validation"]["correct"] for r in vals),
            "high_correct_sum":sum(r["epochs"][-1]["validation"]["correct"] for r in vals)})
    assert rows==frozen["rows"]
    for b in ["low","high"]:
        assert sorted(rows,key=lambda r:(-r[b+"_correct_sum"],r["architecture_id"]))[0]["architecture_id"]==frozen[b+"_selected"]

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--phase",choices=["all","discovery","confirmation"],default="all");parser.add_argument("--resume",action="store_true");args=parser.parse_args()
    setup();verify_freeze();cfg=json.loads((ROOT/"config.json").read_text());fh=digest(ROOT/"artifacts/pre_run_freeze.json")
    if (ROOT/"raw/attempts.jsonl").exists():
        if args.phase!="confirmation" and not args.resume: raise RuntimeError("Existing attempts: no implicit restart or overwrite")
        prior=[json.loads(s) for s in (ROOT/"raw/attempts.jsonl").read_text().splitlines()]
        if any(r["group"]=="confirmation" for r in prior) and not args.resume: raise RuntimeError("Confirmation attempts already exist")
    t,c=time.perf_counter(),time.process_time();tensors=load_data()
    env={"at_utc":stamp(),"python":sys.version,"torch":torch.__version__,"numpy":np.__version__,"platform":platform.platform(),
       "threads":torch.get_num_threads(),"interop_threads":torch.get_num_interop_threads(),"cuda_used":False,
       "data_loading_wall_seconds":time.perf_counter()-t,"data_loading_cpu_seconds":time.process_time()-c}
    stampid=str(time.time_ns())
    save(ROOT/f"artifacts/environment_{args.phase}_{stampid}.json",env)
    total_t,total_c=time.perf_counter(),time.process_time()
    groups=["discovery","confirmation"] if args.phase=="all" else [args.phase]
    for group in groups:
        if group=="confirmation":
            if not (ROOT/"artifacts/selection_freeze.json").exists(): raise ValueError("Selection must freeze before independent retraining")
            verify_freeze();verify_selection(cfg)
        for row in cfg["run_order"]:
            if row["group"]==group: run_one(row["architecture"],row["seed"],group,tensors,cfg,fh,args.resume)
        if group=="discovery" and not (ROOT/"artifacts/selection_freeze.json").exists(): freeze_selections(cfg)
    save(ROOT/f"artifacts/process_{args.phase}_{stampid}.json",{"at_utc":stamp(),"wall_seconds":time.perf_counter()-total_t,"cpu_seconds":time.process_time()-total_c,
      "maxrss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"official_test_evaluations":0,"status":"completed"})

if __name__=="__main__": main()
