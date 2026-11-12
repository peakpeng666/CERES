"""Transparent tie-aware rank statistics, no scipy dependency."""
import math
import numpy as np

def average_ranks(values):
    values=np.asarray(values);order=np.argsort(values,kind='stable');out=np.empty(len(values),dtype=float)
    start=0
    while start<len(order):
        end=start+1
        while end<len(order) and values[order[end]]==values[order[start]]:end+=1
        out[order[start:end]]=(start+end-1)/2+1;start=end
    return out

def spearman(x,y):
    a=average_ranks(x);b=average_ranks(y);a-=a.mean();b-=b.mean();den=math.sqrt(float(a@a)*float(b@b))
    return float(a@b)/den if den else None

def kendall(x,y):
    c=d=tx=ty=both=0
    for i in range(len(x)):
        for j in range(i+1,len(x)):
            a=np.sign(x[i]-x[j]);b=np.sign(y[i]-y[j])
            if a==0 and b==0:both+=1
            elif a==0:tx+=1
            elif b==0:ty+=1
            elif a==b:c+=1
            else:d+=1
    den=math.sqrt((c+d+tx)*(c+d+ty))
    return {'tau_b':(c-d)/den if den else None,'concordant':c,'discordant':d,'x_only_ties':tx,'y_only_ties':ty,'both_ties':both}

def ranks(x,y):return {'kendall':kendall(x,y),'spearman_rho':spearman(x,y)}

def interval(values):
    a=np.asarray([x for x in values if x is not None],dtype=float)
    return {'low':float(np.quantile(a,.025)) if len(a) else None,'high':float(np.quantile(a,.975)) if len(a) else None,'valid_replicates':len(a)}
