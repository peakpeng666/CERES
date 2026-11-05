"""Small, auditable kNN selector for a newly measured local CVRP portfolio.

The frozen historical study is not restored or modified. This model has its own
independent training/calibration/test split and narrow deployment scope.
"""
import hashlib
import json
import math
import statistics as stats
from . import paths
from .algorithm_ids import canonical_algorithm_id, canonical_algorithm_values
paths.ensure_research_path()
from urp_research.cvrp_selection.experiment import features, crc, route

ALGOS = ['pyvrp.ils','ortools.gls']
MODEL = paths.LAB/'models/cvrp-knn-v1.json'
BUDGET = .25
EVALUATED_OBJECTIVE = 'sum_unrounded_euclidean_distance'
EVALUATED_SCOPE = (
    '合成 benchmark：均匀/聚簇生成，20/50/100 客户、容量 50、需求 1–10；'
    '未取整欧氏总距离；每算法 0.25 s、2 次重复。分布、种子或重复次数外推未经验证'
)


def _records_with_current_ids(records):
    return [dict(r, costs=canonical_algorithm_values(r['costs'])) for r in records]


def fit(records):
    records = _records_with_current_ids(records)
    cols=list(zip(*(r['features']['values'] for r in records)))
    mu=list(map(stats.mean,cols));sd=[stats.pstdev(c) or 1 for c in cols]
    means={a:stats.mean(r['costs'][a]/r['features']['star_upper_bound'] for r in records) for a in ALGOS}
    return dict(k=3,mu=mu,sd=sd,sbs=min(ALGOS,key=lambda a:(means[a],a)),train_means=means,
        train=[dict(id=r['id'],scaled=[(x-m)/s for x,m,s in zip(r['features']['values'],mu,sd)],
                    targets={a:r['costs'][a]/r['features']['star_upper_bound'] for a in ALGOS}) for r in records])


def predict(model,instance):
    # Read-only adaptation: callers verify the original artifact hash first.
    model = dict(model, sbs=canonical_algorithm_id(model['sbs']),
                 train_means=canonical_algorithm_values(model.get('train_means', {})),
                 train=[dict(r, targets=canonical_algorithm_values(r['targets'])) for r in model['train']])
    values=features(instance)['values'];scaled=[(v-m)/s for v,m,s in zip(values,model['mu'],model['sd'])]
    neighbors=sorted(model['train'],key=lambda r:(math.dist(r['scaled'],scaled),r['id']))[:model['k']]
    costs={a:stats.mean(r['targets'][a] for r in neighbors) for a in ALGOS}
    selected=min(ALGOS,key=lambda a:(costs[a],a));sbs=model['sbs']
    return dict(chosen=selected,sbs=sbs,score=(costs[sbs]-costs[selected])/costs[sbs],predicted_costs=costs,
                neighbor_ids=[r['id'] for r in neighbors])


def build(records):
    records = _records_with_current_ids(records)
    train=[r for r in records if r['split']=='train'];cal=[r for r in records if r['split']=='calibration']
    model=fit(train);predictions=[]
    for r in cal:
        p=predict(model,r['instance']);p['harm']=r['costs'][p['chosen']]>1.01*r['costs'][p['sbs']];predictions.append(p)
    return dict(model=model,gate=crc(predictions,.1),calibration_predictions=predictions)


def recommend(scenario,instance,budget_s=None):
    if scenario['problem_class']!='euclidean_cvrp':return None
    objective_match=instance.get('objective',EVALUATED_OBJECTIVE)==EVALUATED_OBJECTIVE
    budget_match=(None if budget_s is None else
                  type(budget_s) in (int,float) and math.isfinite(budget_s) and budget_s==BUDGET)
    n=len(instance['demands'])-1
    instance_match=(scenario['frame']['kind']=='benchmark' and n in (20,50,100) and
                    len(instance.get('coordinates',[]))==len(instance['demands']) and
                    instance['capacity']==50 and instance['demands'][0]==0 and
                    all(1<=d<=10 for d in instance['demands'][1:]) and
                    instance.get('depot',0)==0 and instance.get('fleet','unlimited_identical_vehicles')=='unlimited_identical_vehicles')
    scope_metadata=dict(evaluated_objective=EVALUATED_OBJECTIVE,evaluated_budget_s=BUDGET,
        evaluated_repetitions_per_algorithm=2,evaluated_pyvrp_seeds=[11,29],
        evaluated_distribution='synthetic uniform/clustered benchmark; n=20/50/100; capacity=50; demands=1..10',
        objective_match=objective_match,budget_match=budget_match,instance_match=instance_match,
        profile_match=bool(objective_match and budget_match and instance_match),
        caveat='profile_match只核对可见的实例合同、目标与预算；不证明种子、重复次数或分布相同，也不是跨域风险校准')
    unavailable=dict(id='lab.cvrp.knn_crc.v1',status='unavailable',reason='本地训练及独立评估产物尚未生成',
        prediction=None,required=[str(MODEL)],scope=EVALUATED_SCOPE,scope_metadata=scope_metadata,
        objective_match=objective_match,budget_match=budget_match,profile_match=scope_metadata['profile_match'])
    if not MODEL.is_file():return unavailable
    try:
        document=json.loads(MODEL.read_text())
        if document['status']!='evaluated':return unavailable
        payload=json.dumps(document['artifact'],sort_keys=True,separators=(',',':')).encode()
        if hashlib.sha256(payload).hexdigest()!=document['artifact_sha256']:raise ValueError('模型指纹不匹配')
        evaluated=dict(unavailable,model_sha256=document['artifact_sha256'],evaluation=document['evaluation'],budget_s=BUDGET,required=[])
        if not instance_match:
            return dict(evaluated,status='out_of_scope',reason='实例超出合成 benchmark 评估范围；当前回退规则')
        if not objective_match:
            return dict(evaluated,status='out_of_scope',reason='该学习原型仅评估未取整欧氏距离目标；当前目标回退规则')
        if budget_match is not True:
            reason=('未提供实际求解预算，不能将 0.25 s 评估外推到当前请求' if budget_match is None else
                    '实际求解预算不同于 0.25 s 评估预算')
            return dict(evaluated,status='out_of_profile',reason=reason+'；当前回退规则')
        p=predict(document['artifact']['model'],instance);selected,accepted=route(p,document['artifact']['gate'])
        return dict(evaluated,status='available',reason='独立合成样本评估的实验原型；0.25 s/算法、2 次重复（PyVRP 种子 11/29，OR-Tools 未使用种子）。测试集全部回退 PyVRP，未证明推荐增益。种子、重复次数和分布外推未经验证。历史研究数据未恢复。',
            prediction=dict(**p,selected=selected,accepted_alternative=accepted),model_sha256=document['artifact_sha256'],
            evaluation=document['evaluation'],budget_s=BUDGET,required=[])
    except Exception as exc:return dict(unavailable,reason='模型不可用：'+str(exc))
