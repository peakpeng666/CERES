"""Frozen primitives matching original P4. Reads only whitelisted training files."""
from pathlib import Path
import hashlib, itertools, json, os, time
import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[1]
TRAIN_FILES = [f"data_batch_{i}.bin" for i in range(1, 6)]

def digest(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for b in iter(lambda:f.read(1<<20), b""): h.update(b)
    return h.hexdigest()

def stamp(): return time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())

def save(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    tmp.replace(path)

def append(path,value):
    with open(path,"a") as f:
        f.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+"\n");f.flush();os.fsync(f.fileno())

def setup():
    torch.set_num_threads(1);torch.set_num_interop_threads(1)
    torch.use_deterministic_algorithms(True)

def space():
    return [dict(zip(["depth","width","kernel"],v)) for v in itertools.product([2,3],[8,16],[3,5])]

def aid(a): return f"d{a['depth']}_w{a['width']}_k{a['kernel']}"

class TinyCNN(nn.Module):
    def __init__(self, arch):
        super().__init__()
        if arch not in space(): raise ValueError("Architecture outside frozen P4 space")
        layers,channels=[],3
        for layer in range(arch["depth"]):
            out=arch["width"]*(2**layer)
            layers.extend([nn.Conv2d(channels,out,arch["kernel"],padding=arch["kernel"]//2),nn.ReLU(),nn.AvgPool2d(2)])
            channels=out
        layers.extend([nn.AdaptiveAvgPool2d(1),nn.Flatten(),nn.Linear(channels,10)])
        self.layers=nn.Sequential(*layers)
    def forward(self,x): return self.layers(x)

def macs(arch):
    channels,side,count=3,32,0
    for layer in range(arch["depth"]):
        out=arch["width"]*(2**layer)
        count+=side*side*out*channels*arch["kernel"]**2
        channels,side=out,side//2
    return count+channels*10

def load_data():
    old=ROOT/"evidence/original_p4/artifacts"
    manifest=json.loads((old/"data_manifest.json").read_text())
    assert digest(old/"split.json")==manifest["split_sha256"]
    split=json.loads((old/"split.json").read_text())
    batches=[]
    for name in TRAIN_FILES:
        path=ROOT/"data"/name
        assert digest(path)==manifest["extracted_files"][name]["sha256"]
        batches.append(np.memmap(path,mode="r",dtype=np.uint8,shape=(10000,3073)))
    tensors={}
    for key in ["train","validation"]:
        rows=np.stack([batches[i//10000][i%10000] for i in split[key]])
        tensors[key]=(torch.from_numpy(rows[:,1:].copy()).reshape(-1,3,32,32).float().div_(255),torch.from_numpy(rows[:,0].copy()).long())
    return tensors

@torch.no_grad()
def evaluate(model,data,batch=128):
    model.eval();x,y=data;correct=0;loss=0.0;predictions=[]
    for i in range(0,len(y),batch):
        logits=model(x[i:i+batch]);pred=logits.argmax(1)
        correct+=int((pred==y[i:i+batch]).sum())
        loss+=float(nn.functional.cross_entropy(logits,y[i:i+batch],reduction="sum"))
        predictions.extend(pred.tolist())
    return {"correct":correct,"n":len(y),"accuracy":correct/len(y),"loss":loss/len(y)},predictions
