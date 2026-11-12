"""Render a Chinese report without changing any experiment evidence."""
import json
from core import ROOT

def pct(x):return f'{100*x:.2f}%'
def num(x):return '未定义' if x is None else f'{x:.4f}'

s=json.loads((ROOT/'reports/summary.json').read_text());sel=s['selection'];cfg=json.loads((ROOT/'config.json').read_text())
audit=json.loads((ROOT/'audit/results_audit.json').read_text())
assert audit['passed'] and len(audit['independent_checkpoint_endpoints'])==160
lines=['# P4：2轮/20轮保真度与独立重训结果','',
'## 结论','',
f"固定8种原P4 CNN、原4000训练/1000验证图，5个新发现seed的2轮均值选择 **{sel['low']}**，20轮均值选择 **{sel['high']}**。两端点top1{'相同' if sel['same_top1'] else '不同'}。随后对全部8结构用另外5个seed从零重训20轮，两种选择的确认均值为 **{pct(sel['low_confirmation_accuracy'])} / {pct(sel['high_confirmation_accuracy'])}**；相对确认组经验最好结构的top1后悔为 **{sel['low_regret_pp']:.2f} / {sel['high_regret_pp']:.2f} 个百分点**。",'',
f"2轮与20轮发现组均值排名的Kendall τ-b={num(s['correlations']['discovery_2_vs_20']['kendall']['tau_b'])}、Spearman ρ={num(s['correlations']['discovery_2_vs_20']['spearman_rho'])}。确认组经验最好是{sel['confirmation_empirical_best']}，均值{pct(sel['reference_accuracy'])}。这些是当前八结构、固定训练配方和固定validation的实测结果；20轮没有收敛证明，确认独立性限于新训练seed，不能叫独立测试泛化或全NAS复现。",'',
'## 1. 为什么补这一项','',
'M15对P176/P408的精读及全库映射指出：较短训练的好排名不自动等于较长独立训练后的质量。原P4只跑2轮；原5000图选优后评估沿用旧权重，回答的是固定模型的样本表现，未检验新初始化重训。本轮补的是这一缺口。P176还有共享权重实验，但本轮完全没有共享权重，不能声称复现了其共享权重机制或整篇结论。',
'原文依据：[P176附录D.2](https://arxiv.org/html/1902.08142v3#A4.SS2)、[P408](https://arxiv.org/html/1902.07638v3)。已有M15笔记是继承证据，本轮回查关键原文，并非重新全文通读全库。','',
'## 2. 已冻结的设计','',
'- 8结构：depth {2,3} × width {8,16} × kernel {3,5}，无增删或事后挑结构。Conv→ReLU→AvgPool、末尾全局平均池化和10类线性层均与原P4一致。',
'- Adam(lr=.003, weight_decay=.0001)，batch128，无增强，32×32 RGB仅/255，PyTorch2.14.1+cpu / NumPy2.5.3，CPU单线程确定性算子。',
'- 发现seed310001–310005；确认seed320001–320005。每个seed覆盖全部8结构、每条轨迹20轮。发现第2/20轮先选top1并写冻结凭据，再启动任何确认训练；确认全8结构，避免只确认赢家。',
'- 共80条新初始化轨迹、1600训练epoch、51200次优化器更新、640万训练图呈现；2轮是同一20轮轨迹前缀，不另外计为新训练。',
'- 选择使用固定端点的5seed validation准确数总和；平分按结构ID字典序，相关系数使用真实同分平均秩。无早停、无best-epoch回选、无挑seed。',
'- 5seed均值选择比原P4单候选单seed评分更贵，均值结果不能直接归给原RL控制器的单seed奖励；原RL和随机搜索没有在本轮重新比较。','',
'## 3. 八种结构的完整结果','',
'下表“±”是5个训练seed的样本标准差，不是跨数据集置信区间。确认仍用同一1000张validation；8结构是有限总体，不能将图片/epoch当独立NAS实验。','',
'| 结构 | 参数数 | 发现2轮 | 发现20轮 | 确认20轮 | 名次：2/20/确认 |',
'|---|---:|---:|---:|---:|---:|']
for r in s['architectures']:
    lines.append(f"| {r['architecture_id']} | {r['parameters']} | {pct(r['discovery_2_mean_accuracy'])} ± {100*r['discovery_2_seed_std']:.2f}pp | {pct(r['discovery_20_mean_accuracy'])} ± {100*r['discovery_20_seed_std']:.2f}pp | {pct(r['confirmation_20_mean_accuracy'])} ± {100*r['confirmation_20_seed_std']:.2f}pp | {r['discovery_2_rank']:g}/{r['discovery_20_rank']:g}/{r['confirmation_20_rank']:g} |")
lines+=['','## 4. 排名与选择不确定性','','| 比较 | Kendall τ-b | Spearman ρ | τ描述性95%区间 |','|---|---:|---:|---|']
for name,c in s['correlations'].items():
    b=s['bootstrap_intervals'][name+'_tau'];lines.append(f"| {name} | {num(c['kendall']['tau_b'])} | {num(c['spearman_rho'])} | [{num(b['low'])}, {num(b['high'])}] |")
lines+=['','每个发现seed的结果：','','| seed | 2轮top1 | 20轮top1 | τ-b | ρ | 2轮top1确认经验后悔pp |','|---:|---|---|---:|---:|---:|']
for r in s['per_discovery_seed']:
    c=r['rank_correlation'];lines.append(f"| {r['seed']} | {r['low_top1']} | {r['high_top1']} | {num(c['kendall']['tau_b'])} | {num(c['spearman_rho'])} | {r['low_top1_confirmation_empirical_regret_pp']:.2f} |")
lines+=['',f"确认组上，固定20轮选择减固定2轮选择的配对均值差为{sel['high_minus_low_pp']:+.2f}pp。5个确认seed的逐项差为："+'；'.join(f"{r['seed']}: {r['high_minus_low_pp']:+.2f}pp" for r in s['confirmation_paired_seed_differences'])+'。','']
for k,label in [('low_regret_pp','2轮选择经验后悔'),('high_regret_pp','20轮选择经验后悔'),('high_minus_low_pp','20轮选择减2轮选择')]:
    b=s['bootstrap_intervals'][k];lines.append(f"- {label}描述性95%区间：[{b['low']:.2f}, {b['high']:.2f}]pp")
lines+=['',
'所有区间使用冻结的10000次seed-block bootstrap。相关性保持同一发现seed内低高端点配对，确认组独立抽seed；后悔及配对差固定实际top1，仅重抽确认seed。5seed区间只描述此划分下训练随机性，不能据“不跨0”宣称跨数据集显著优越。确认组经验最大值本身会乐观，因此这里报告经验后悔，不是真实最优后悔。',
f"同空间均匀随机单架构在确认组的平均准确率为{pct(sel['uniform_random_mean_accuracy'])}，期望经验后悔{sel['uniform_random_expected_empirical_regret_pp']:.2f}pp。这只是有限空间描述性参照，不是新跑的同预算随机搜索。",'',
'## 5. 完整成本','',
'| 阶段 | 轨迹/epoch | 训练图呈现 | 验证图呈现 | 轨迹墙钟/CPU秒 |','|---|---:|---:|---:|---:|']
for g,c in s['costs'].items():
    lines.append(f"| {g} | {c['runs']}/{c['epochs']} | {c['train_presentations']:,} | {c['validation_presentations']:,} | {c['run_wall_seconds']:.3f}/{c['run_cpu_seconds']:.3f} |")
ac=s['attempt_cost_accounting']
lines+=['',f"正式attempts：started {ac['started_count']}，completed {ac['completed_count']}，failed {ac['failed_count']}，orphan started {ac['orphan_started_count']}，恢复事件{ac['resumed_events']}。全部已记录attempt墙钟/CPU为{ac['known_attempt_wall_seconds']:.3f}/{ac['known_attempt_cpu_seconds']:.3f}秒；其中已知失败成本{ac['failed_wall_seconds']:.3f}/{ac['failed_cpu_seconds']:.3f}秒。计时完整标志：{ac['complete']}。若发生中断，无法恢复的部分epoch成本保持未知，不能当零。",'',
'只做端点评分的嵌套逻辑对照（构建+相应前缀训练+一次端点验证，不含序列化/额外诊断）：']
for b,c in s['logical_selection_cost'].items():lines.append(f"- {b}轮：{c['epochs']}训练epoch、{c['train_presentations']:,}训练图呈现+{c['endpoint_validation_presentations']:,}端点评分，墙钟/CPU {c['wall_seconds']:.3f}/{c['cpu_seconds']:.3f}秒。")
lines+=['',
'上述逻辑2轮成本包含在20轮轨迹中，不能再次加总。实际研究始终支付了两组80×20训练及每epoch诊断验证；160万验证呈现是反复读取同1000张图，不是160万独立样本。逐轮训练/验证计时和卷积/线性前向MAC在summary/CSV中全列，MAC没有包含反向、池化、优化器或I/O，不叫总FLOPs。',
'单列的准备费用包括官方数据恢复、依赖安装、synthetic smoke、单元测试和独立检查。数据曾有下载重叠/中断与一次HTTP403，失败日志及事件记录保留，未将未核验partial用于任何训练；部分安装/初次传输没有完整CPU计时，明确未知。清洁下载及已确认前进程停止后的续传shell time分别在data_download_clean_retry_time.log和data_download_final_resume_time.log；非评分smoke计时在smoke_timing.json，不能说准备免费。','',
'## 6. 数据隔离与独立验收','',
'原CIFAR binary archive、五训练batch和split均与原P4哈希一致。原4000/1000索引不变；原5000图postselection评估及剩余40000图本轮不训练/推理。官方test仅作为官方压缩包内未使用成员存在，不提取、不样本解析、不推理、不选择；没有测试准确率。',
f"独立审核另实现CNN与统计，不调用主评价函数；对全部160端点以batch73重算1000图的预测和loss，并核Adam step、freeze、selection前后时间顺序、80轨迹/1600epoch/51200更新、160端点hash、原124文件未修改。此审核额外160000图validation推理，完整审核墙钟/CPU为{audit['wall_seconds']:.3f}/{audit['cpu_seconds']:.3f}秒，含指标重算与bootstrap，不只是推理。详见audit结果JSON和中文验收。正式模型无新增训练。",
'本地协议冻结是可审计的执行前固定，不是外部公开预注册。详见pre_run_freeze.json及selection_freeze_receipt.json。','',
'## 7. 可以和不能据此推断什么','',
'- 可以：判断这八个既定小CNN在同一开发子集与配方中，早期评分的排序/选中结构能否保持到更高预算和新初始化；发现组与确认组区别有真实训练证据。',
'- 不能：证明RL优于随机；证明20轮收敛；给出官方test泛化；把5seed均值可靠性当单次reward可靠性；把本轮称P176/P408完整复现或任何共享权重结论的复现。',
'- 后续是否扩大训练数据、预算或正式测试需要另行冻结。本轮不会为了得到更好排名、准确率或后悔值去改终点/换seed。','',
'代码、完整原始记录、预测、检查点、重跑命令和独立审核见同包README。']
(ROOT/'reports/结果报告_中文.md').write_text('\n'.join(lines)+'\n')
print(ROOT/'reports/结果报告_中文.md')
