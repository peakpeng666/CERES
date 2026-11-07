# 参数目录

由 `tools/export_agri_catalog.py` 从目录生成，版本 `c763479346a9`。模型说明见[田间作业模型](model.md)。

## 车辆

| 平台 | 类别 | 层级 | 长 m | 宽 m | 轮距 m | 质量 kg | 转弯半径 m | 作业速度上限 km/h | 续航 h |
|---|---|---|---|---|---|---|---|---|---|
| 田间差速机器人（工作台参考模型） | 差速 | 可仿真 | 0.8 · 项目模型 | 0.96 · 项目模型 | 0.66 · 项目模型 | 25 · 项目模型 | 0 · 项目模型 | 3.6 · 项目模型 | 4 · 假设 |
| Clearpath Husky A300 | 滑移转向 | 可规划 | 1 · 假设 | 0.7 · 假设 | 0.55 · 假设 | 75 · 假设 | 0 · 假设 | 7.2 · 厂商资料 | 3 · 假设 |
| Clearpath Warthog | 滑移转向 | 可规划 | 1.5 · 假设 | 1.4 · 假设 | 1.1 · 假设 | 280 · 假设 | 0 · 假设 | 18 · 厂商资料 | 3 · 假设 |
| Naïo Oz | 差速 | 可规划 | 1.3 · 假设 | 0.5 · 假设 | 0.45 · 假设 | 150 · 假设 | 0 · 假设 | 1.8 · 假设 | 8 · 厂商资料 |
| Naïo Orio（已停产） | 阿克曼 | 可规划 | 3 · 假设 | 2 · 假设 | 1.6（1.5–1.75） · 厂商资料 | 1500 · 假设 | 2.5 · 假设 | 5.6 · 厂商资料 | 12 · 厂商资料 |
| Agrointelli Robotti 150D | 滑移转向 | 可规划 | 4 · 假设 | 3 · 假设 | 2.5 · 假设 | 3150 · 厂商资料 | 0 · 厂商资料 | 10 · 假设 | 20 · 假设 |
| AgXeed AgBot 5.115T2 | 履带 | 可规划 | 4.6 · 假设 | 2.85（2.35–2.85） · 厂商资料 | 2.5（1.9–3.2） · 厂商资料 | 7000 · 假设 | 3 · 假设 | 11 · 厂商资料 | 20 · 厂商资料 |
| John Deere 5075GN 窄型拖拉机 | 阿克曼 | 可规划 | 4.319 · 厂商资料 | 1.257 · 厂商资料 | 1 · 假设 | 2660（2530–2975） · 厂商资料 | 3.627 · 厂商资料 | 12 · 假设 | 10 · 假设 |
| 120 kW 轮式拖拉机（类别模板） | 阿克曼 | 可规划 | 4.8 · 假设 | 2.4 · 假设 | 1.9 · 假设 | 6500 · 假设 | 5.5 · 假设 | 16 · 假设 | 12 · 假设 |
| GUSS 果园自动喷雾机 | 阿克曼 | 可规划 | 6 · 假设 | 2.32 · 厂商资料 | 2.07 · 厂商资料 | 6000 · 假设 | 4.5 · 假设 | 8 · 假设 | 10 · 假设 |
| 极飞 R150 无人车 | 滑移转向 | 可规划 | 1.4 · 假设 | 1.2 · 假设 | 1 · 假设 | 250 · 假设 | 0 · 假设 | 4.3 · 厂商资料 | 3 · 假设 |
| DJI Agras T50 植保无人机 | 无人机 | 可规划 | 2.8 · 厂商资料 | 3.085 · 厂商资料 | 0 · 厂商资料 | 92 · 厂商资料 | 0 · 假设 | 25 · 假设 | 0.15 · 假设 |
| 自走式联合收获机（类别模板） | 阿克曼 | 可规划 | 9 · 假设 | 3.3 · 假设 | 2.8 · 假设 | 15000 · 假设 | 7 · 假设 | 6.5 · 标准 | 10 · 假设 |
| 水田履带底盘（类别模板） | 履带 | 可规划 | 3.2 · 假设 | 1.8 · 假设 | 1.3 · 假设 | 1500 · 假设 | 1.8 · 假设 | 6 · 假设 | 8 · 假设 |
| 温室轨道作业车（类别模板） | 轨道 | 展示 | 1.4 · 假设 | 0.7 · 假设 | 0.55 · 假设 | 180 · 假设 | 0 · 假设 | 2.5 · 假设 | 6 · 假设 |

## 机具

| 机具 | 类别 | 连接 | 作业 | 幅宽 m | 质量 kg | 容量 |
|---|---|---|---|---|---|---|
| 小型喷杆模块 | 植保 | deck | 植保（全田喷施） | 1.5（1.0–3.0） · 假设 | 25 · 假设 | 20 L（10–40） · 假设 |
| 视觉巡检模组 | 感知 | deck | 逐株检查、果园修剪 | 1（0.5–2.0） · 假设 | 5 · 假设 | — |
| 小型机械除草工具 | 除草 | deck | 除草（机械）、中耕 | 0.8（0.4–1.0） · 厂商资料 | 40 · 假设 | — |
| 悬挂喷杆喷雾机 | 植保 | 3pt | 植保（全田喷施） | 12（6–18） · 假设 | 1400 · 假设 | 800 L（400–1200） · 假设 |
| 牵引喷杆喷雾机 | 植保 | drawbar | 植保（全田喷施） | 24（18–36） · 假设 | 5500 · 假设 | 3000 L（2000–5000） · 假设 |
| 风送果园喷雾机 | 植保 | drawbar | 植保（全田喷施） | 4（2–6） · 假设 | 2200 · 假设 | 1500 L（600–2000） · 假设 |
| 串联圆盘耙 | 耕整 | 3pt | 耕整地 | 3（2–6） · 假设 | 1200 · 假设 | — |
| 铧式犁 | 耕整 | 3pt | 耕整地 | 1.6（0.8–3.0） · 假设 | 1100 · 假设 | — |
| 旋耕机 | 耕整 | 3pt | 耕整地 | 2.5（1.2–4.0） · 假设 | 800 · 假设 | — |
| 行间中耕机 | 中耕除草 | 3pt | 中耕、除草（机械） | 4.5（1.5–9.0） · 假设 | 700 · 假设 | — |
| 条播机 | 播种 | 3pt | 播种 | 4（2.5–6.0） · 假设 | 1500 · 假设 | 600 kg（300–1200） · 假设 |
| 精量播种机（6 行） | 播种 | 3pt | 播种 | 4.5（3.0–9.0） · 假设 | 1300 · 假设 | 200 kg（100–400） · 假设 |
| 离心撒肥机 | 施肥 | 3pt | 施肥（变量追肥） | 18（12–24） · 假设 | 1400 · 假设 | 1000 kg（500–2000） · 假设 |
| 圆盘割草机 | 收获 | 3pt | 割草 | 3（2.0–4.0） · 假设 | 700 · 假设 | — |
| 秸秆粉碎机 | 收获 | 3pt | 秸秆处理 | 2.5（1.5–4.0） · 假设 | 800 · 假设 | — |
| 旋转搂草机 | 收获 | 3pt | 搂草 | 4（3.0–7.0） · 假设 | 600 · 假设 | — |
| 圆捆打捆机 | 收获 | drawbar | 打捆 | 2.2（1.8–2.5） · 假设 | 3000 · 假设 | — |
| 谷物割台（一体） | 收获 | integrated | 联合收获 | 6（4.0–9.0） · 假设 | 1800 · 假设 | 7000 kg（5000–12000） · 假设 |
| 乘坐式插秧单元（8 行） | 移栽 | 3pt | 移栽 | 2.4（1.2–3.0） · 假设 | 350 · 假设 | 48 盘（24–72） · 假设 |
| 一体风送喷雾系统 | 植保 | integrated | 植保（全田喷施） | 4（2–6） · 假设 | 2300 · 假设 | 2271 L · 厂商资料 |
| 一体弥雾系统 | 植保 | integrated | 植保（全田喷施） | 6（3–12） · 厂商资料 | 150 · 假设 | 100 L（100–150） · 厂商资料 |
| 无人机喷洒系统 | 植保 | integrated | 植保（全田喷施） | 8（4–11） · 厂商资料 | 40 · 厂商资料 | 40 L（40–50） · 厂商资料 |
| 推料板 | 畜牧 | deck | 推料 | 1（0.6–1.5） · 假设 | 20 · 假设 | — |
| 清粪刮板 | 畜牧 | deck | 圈舍清扫 | 1.5（1.0–2.5） · 假设 | 25 · 假设 | 300 kg（100–500） · 假设 |

## 作业

| 作业 | 模型 | 阶段 | 资源 | 速度 km/h | 田间效率 |
|---|---|---|---|---|---|
| 耕整地 | 作业行 | pre_crop | — | 10（6.5–11.0） · 标准 | 0.8（0.7–0.9） · 标准 |
| 播种 | 作业行 | pre_crop | 种子 | 8（6.5–11.0） · 标准 | 0.7（0.55–0.8） · 标准 |
| 移栽 | 作业行 | pre_crop | 秧苗 | 3（1.5–5.0） · 假设 | 0.6（0.45–0.7） · 假设 |
| 中耕 | 作业行 | in_crop | — | 8（5.0–11.0） · 标准 | 0.8（0.7–0.9） · 标准 |
| 植保（全田喷施） | 作业行 | in_crop | 药液 | 10.5（5.0–11.5） · 标准 | 0.65（0.5–0.8） · 标准 |
| 植保（选择性/斑块） | 点任务 | in_crop | 药液 | — | — |
| 植保（逐株定点） | 点任务 | in_crop | 药液 | — | — |
| 除草（机械） | 作业行 | in_crop | — | 2.5（1.0–5.0） · 厂商资料 | 0.75（0.6–0.85） · 假设 |
| 施肥（变量追肥） | 作业行 | in_crop | 肥料 | 11（8.0–16.0） · 标准 | 0.7（0.6–0.8） · 标准 |
| 土壤及作物采样 | 点任务 | any | 样本 | — | — |
| 环境监测 | 点任务 | any | — | — | — |
| 逐株检查 | 作业行 | in_crop | — | 2.5（1.0–4.0） · 假设 | 0.8（0.65–0.9） · 假设 |
| 灌溉设施巡检 | 点任务 | any | — | — | — |
| 采收 | 点任务 | harvest | 果实 | — | — |
| 果园修剪 | 作业行 | in_crop | — | 3（1.5–5.0） · 假设 | 0.7（0.55–0.8） · 假设 |
| 接粮与运输 | 协同 | harvest | 粮食 | — | — |
| 联合收获 | 作业行 | harvest | 粮食 | 5（3.0–6.5） · 标准 | 0.7（0.65–0.8） · 标准 |
| 草捆收集 | 点任务 | harvest | 草捆 | — | — |
| 秸秆处理 | 作业行 | harvest | — | 8（6.5–11.5） · 标准 | 0.8（0.7–0.9） · 标准 |
| 割草 | 作业行 | harvest | — | 11（8.0–19.0） · 标准 | 0.8（0.75–0.9） · 标准 |
| 搂草 | 作业行 | harvest | — | 10（6.5–13.0） · 标准 | 0.8（0.7–0.9） · 标准 |
| 打捆 | 作业行 | harvest | — | 8（5.0–13.0） · 标准 | 0.65（0.55–0.75） · 标准 |
| 苗木及物料搬运 | 点任务 | any | 物料 | — | — |
| 饲喂 | 点任务 | any | 饲料 | — | — |
| 推料 | 作业行 | any | — | 3（1.5–5.0） · 假设 | 0.75（0.6–0.85） · 假设 |
| 圈舍清扫 | 作业行 | any | 粪污 | 3（1.5–5.0） · 假设 | 0.75（0.6–0.85） · 假设 |
| 补水补药（站点补给） | 支持 | any | — | — | — |
| 充电（充电桩调度） | 支持 | any | — | — | — |
| 卸载（港池/粮库排队） | 支持 | any | — | — | — |
| 入库与转场 | 支持 | any | — | — | — |

## 环境

| 环境 | 结构 | 平台 | 作业行作业 |
|---|---|---|---|
| 大田与旱作地 | swath | 差速、滑移转向、阿克曼、履带、无人机 | 耕整地、播种、中耕、植保（全田喷施）、除草（机械）、施肥（变量追肥）、逐株检查、联合收获、秸秆处理 |
| 水田 | swath | 履带、无人机 | 耕整地、移栽、植保（全田喷施）、施肥（变量追肥）、联合收获、秸秆处理 |
| 果园 | alley | 差速、滑移转向、阿克曼、履带、无人机 | 中耕、植保（全田喷施）、除草（机械）、施肥（变量追肥）、逐株检查、果园修剪、割草 |
| 葡萄园 | alley | 差速、滑移转向、阿克曼、履带、无人机 | 中耕、植保（全田喷施）、除草（机械）、逐株检查、果园修剪、割草 |
| 茶园 | alley | 差速、滑移转向、履带、无人机 | 植保（全田喷施）、除草（机械）、施肥（变量追肥）、逐株检查、果园修剪 |
| 设施温室 | alley | 差速、滑移转向、轨道 | 移栽、植保（全田喷施）、除草（机械）、逐株检查、果园修剪 |
| 苗圃 | alley | 差速、滑移转向 | 移栽、植保（全田喷施）、除草（机械）、施肥（变量追肥）、逐株检查 |
| 丘陵梯田 | swath | 履带、无人机 | 耕整地、移栽、植保（全田喷施）、联合收获 |
| 牧草地与牧场 | swath | 差速、滑移转向、阿克曼、履带、无人机 | 耕整地、播种、植保（全田喷施）、施肥（变量追肥）、割草、搂草、打捆 |
| 畜禽养殖设施 | alley | 差速、滑移转向、轨道 | 推料、圈舍清扫 |
| 农场仓储与装卸区 | none | 差速、滑移转向、阿克曼 | — |
| 多田块农场及连接路网 | swath | 差速、滑移转向、阿克曼、履带 | 耕整地、播种、中耕、植保（全田喷施）、除草（机械）、施肥（变量追肥）、联合收获、秸秆处理 |
| 水产养殖水面 | none | 船艇、无人机 | — |
| 农业空域（无人机） | swath | 无人机 | 植保（全田喷施） |

## 来源

| 编号 | 类型 | 核对 | 出处 | 范围 |
|---|---|---|---|---|
| `asae_d497_5` | 标准 | 原文 | [ASAE D497.5 FEB2006 Agricultural Machinery Management Data（表 1 牵引阻力、表 2 旋转功率、表 3 田间效率与速度）](https://faculty.uobasrah.edu.iq/uploads/teaching/1722757166.pdf) | 北美大田机械的典型值；现行版本为 D497.7，数值需按地区复核 |
| `bochtis2008` | 论文 | 原文 | [Bochtis & Vougioukas, Minimising the non-working distance travelled by machines operating in a headland field pattern, Biosystems Engineering 101 (2008)](https://doi.org/10.1016/j.biosystemseng.2008.06.008) | U/P 型地头转弯长度式与非作业里程目标；平行作业行、无滑移 |
| `hossain2026` | 论文 | 原文 | [Hossain & Zahin, Exact and Heuristic Methods for Capacitated Path Planning for Agricultural Field Operations, GECCO 2026](https://doi.org/10.1145/3795095.3805120) | 容量约束作业行排序、giant-tour+split；算例设定 48 m 幅宽、20/15 km/h、18 m 转弯半径、8000 L、125 L/ha |
| `lopez2025` | 论文 | 原文 | [López-Sánchez et al., Capacitated Agriculture Fleet Vehicle Routing with Implements and Limited Autonomy, ICRA 2025](https://hal.science/hal-05443281v1) | 车辆-机具兼容、机具容量与电量续航进入路由模型 |
| `moghadam2020` | 论文 | 原文 | [Khosravani Moghadam et al., An Arable Field for Benchmarking of Metaheuristic Algorithms for Capacitated Coverage Path Planning Problems, Agronomy 2020](https://doi.org/10.3390/agronomy10101454) | 8 行田块的枚举最优基准：高/低容量与近/远库房四情景 |
| `wan2023` | 论文 | 原文 | [Wan et al., The Agricultural Spraying Vehicle Routing Problem With Splittable Edge Demands, ITOR 2023](https://arxiv.org/abs/2308.15108) | 果园喷施的可拆分边需求；本轮未实现行内拆分 |
| `mier2025soil2cover` | 论文 | 检索摘要 | [Mier et al., Soil2Cover: Coverage path planning minimizing soil compaction for sustainable agriculture, Precision Agriculture 2025](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12134033/) | 用 SoilFlex 把压实代价写进覆盖路径；本轮只取几何碾压比，未接入土壤力学 |
| `augustin2022` | 论文 | 检索摘要 | [Augustin et al., Effects of varying field geometry and machine configurations on spatial field traffic, Soil Use and Management 2022](https://macau.uni-kiel.de/servlets/MCRFileNodeServlet/macau_derivate_00006122/Soil%20Use%20and%20Management%20-%202022%20-%20Augustin%20-%20Effects%20of%20varying%20field%20geometry%20and%20machine%20configurations%20on%20spatial%20field.pdf) | 转述 3 Mg（Horn 2003）与 5 Mg 的轮载临界值 |
| `raper2006` | 论文 | 检索摘要 | [Raper, Agricultural traffic impacts on soil, USDA-ARS 2005/2006](https://www.ars.usda.gov/ARSUserFiles/60100500/csr/researchpubs/raper/raper_06d.pdf) | 塑限时预压强度约 100 kPa，与中型农机接地应力相当；不是法规限值 |
| `umass_apple` | 推广资料 | 检索摘要 | [UMass/Cornell high-density apple orchard guidance](https://ag.umass.edu/sites/ag.umass.edu/files/pdf-doc-ppt/highdensityapple.pdf) | 高密度苹果行距 10–14 ft（3.0–4.3 m），树高约为通道宽 90% |
| `dji_t50` | 厂商资料 | 原文 | [DJI Agras T50 规格页](https://ag.dji.com/t50/specs) | 药箱 40/50 L、有效喷幅 4–11 m（3 m 作业高度）、流量 16/24 L/min、起飞重量 92/103 kg；未列悬停时间 |
| `naio_oz` | 厂商资料 | 原文 | [Naïo Oz 产品页](https://www.naio-technologies.com/en/oz-robot/) | 提升 60 kg、作业宽度至 100 cm、续航至 8 h；未列车身尺寸 |
| `naio_orio` | 厂商资料 | 检索摘要 | [Naïo Orio 产品页（官方声明已停止供货与支持）](https://www.naio-technologies.com/en/orio-robot/) | 轮距 1.50–1.75 m、提升 700 kg、约 9 ha/天；5.6 km/h 来自 Future Farming |
| `agxeed_5115t2` | 厂商资料 | 检索摘要 | [AgXeed AgBot 5.115T2 产品页](https://www.agxeed.com/?p=4523) | 115 kW、轨距 1.90–3.20 m（宣传册另载 3.00 m 上限）、350 L 柴油、作业 2.5–11 km/h、75% 负载约 20 h |
| `robotti_150d` | 厂商资料 | 检索摘要 | [Agrointelli Robotti 150D 产品单页](https://www.abemec.nl/wp-content/uploads/folder-robotti-150d.pdf) | 干重 3150 kg、三点提升 750 kg、2×100 L 柴油、两台 Kubota 共 144 hp；未列轮距 |
| `jd_5075gn` | 厂商资料 | 检索摘要 | [John Deere 5075GN 窄型拖拉机（经销商规格页）](https://www.ritchiespecs.com/model/john-deere-5075gn-mfwd-tractor) | 转弯半径 3627 mm、宽 49.5 in、轴距 2098 mm、长 4319 mm、PTO 61 hp、提升 2549 kg、重 2530–2975 kg（随配置） |
| `guss_orchard` | 厂商资料 | 检索摘要 | [GUSS 果园自动喷雾机（Future Farming 规格摘要 / New Atlas）](https://www.futurefarming.com/guss-autonomous-orchard-sprayer/) | 宽约 2.32 m、轮距 2.07 m、药箱 600 gal；第三方转述 |
| `clearpath_husky` | 厂商资料 | 检索摘要 | [Clearpath Husky 规格对比](https://clearpathrobotics.com/husky-spec-comparison/) | A300：2.0 m/s、100 kg 载荷 |
| `clearpath_warthog` | 厂商资料 | 检索摘要 | [Clearpath Warthog 数据表](https://www.generationrobots.com/media/Warthog_DataSheet.pdf) | 18 km/h、载荷 272 kg |
| `xag_r150` | 厂商资料 | 检索摘要 | [XAG R150 无人车](https://xa.com/en/r150-2022) | 药箱 100–150 L（来源不一）、喷幅约 12 m、最高 1.2 m/s |
| `project_field_robot` | 项目模型 | 原文 | 本项目田间差速参考模型 urp.field-diffdrive.rigid/6 | Gazebo 与 MuJoCo 共用的未标定刚体模型 |
| `assumption` | 假设 | 原文 | 本项目工程假设 | 证据不足时的可调默认值，界面按「假设」标注 |
