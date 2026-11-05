import { lazy, Suspense } from 'react';
import { Spinner } from '../lab/ui';
import { ViewErrorBoundary } from '../lab/shell/Page';
import { ReplayPanel } from './experiment/ReplayPanel';
import { AdvisorPanel, ComparePanel, InspectorPanel, MapPanel, VerifyPanel } from './panels/PlanPanels';
import { ScenarioNavigator } from './panels/LibraryPanels';
import { ConsolePanel, LegacyPlan, RunsPanel, ServicesPanel, SimPanel } from './panels/SystemPanels';
import { useStudio } from './store';
import { AgentChatPanel } from '../agent/AgentChat';

const lib = () => import('./panels/LibraryPanels');
const research = () => import('./panels/ResearchPanels');
const system = () => import('../desk/SystemPages');
const PhysicsPanel = lazy(() => import('../desk/PhysicsPanel').then((m) => ({ default: m.PhysicsPanel })));
const AlgorithmsPanel = lazy(() => lib().then((m) => ({ default: m.AlgorithmsPanel })));
const CodePanel = lazy(() => lib().then((m) => ({ default: m.CodePanel })));
const CvrplibPanel = lazy(() => lib().then((m) => ({ default: m.CvrplibPanel })));
const FieldsPanel = lazy(() => lib().then((m) => ({ default: m.FieldsPanel })));
const ReferencesPanel = lazy(() => lib().then((m) => ({ default: m.ReferencesPanel })));
const StudiesPanel = lazy(() => research().then((m) => ({ default: m.StudiesPanel })));
const FieldSimPanel = lazy(() => import('./panels/FieldSimPanel').then((m) => ({ default: m.FieldSimPanel })));
const RosView = lazy(() => import('./ros/RosScene').then((m) => ({ default: m.RosView })));
const GazeboView = lazy(() => import('./ros/GazeboView').then((m) => ({ default: m.GazeboView })));
const TelemetryView = lazy(() => import('./ros/Telemetry').then((m) => ({ default: m.TelemetryView })));
const AnalysisPanel = lazy(() => research().then((m) => ({ default: m.AnalysisPanel })));
const AssetLibrary = lazy(() => import('./assets/AssetLibrary').then((m) => ({ default: m.AssetLibrary })));
const AssetPreview = lazy(() => import('./assets/AssetPreview').then((m) => ({ default: m.AssetPreview })));
const AssetInspector = lazy(() => import('./assets/AssetInspector').then((m) => ({ default: m.AssetInspector })));
const AgentTaskPanel = lazy(() => import('../agent/TaskPanel').then((m) => ({ default: m.AgentTaskPanel })));
const ExperimentPanel = lazy(() => import('./experiment/ExperimentPanel').then((m) => ({ default: m.ExperimentPanel })));
const DecisionPanel = lazy(() => import('./experiment/DecisionPanel').then((m) => ({ default: m.DecisionPanel })));
const FiguresPanel = lazy(() => import('./figures/FiguresPanel').then((m) => ({ default: m.FiguresPanel })));
const ModelsPage = lazy(() => import('../desk/ModelsPage').then((m) => ({ default: m.ModelsPage })));
const CapabilitiesPage = lazy(() => system().then((m) => ({ default: m.CapabilitiesPage })));
const DataPage = lazy(() => import('../desk/DesktopPages').then((m) => ({ default: m.DataPage })));
const DesktopPage = lazy(() => import('../desk/DesktopPages').then((m) => ({ default: m.DesktopPage })));
const ToolsPage = lazy(() => system().then((m) => ({ default: m.ToolsPage })));
const EvalsPage = lazy(() => system().then((m) => ({ default: m.EvalsPage })));

function ClassicPanel() {
  const id = useStudio((x) => x.scenarioId);
  return id ? <LegacyPlan id={id} /> : null;
}

/** Every workbench panel, once. The desk tabs and the free layout render from this same list. */
export const PANELS = {
  agentChat: { title: '智能体', C: AgentChatPanel },
  agentTask: { title: '任务与执行', C: AgentTaskPanel },
  map: { title: '地图', C: MapPanel },
  inspector: { title: '检查器', C: InspectorPanel },
  advisor: { title: '推荐与求解', C: AdvisorPanel },
  compare: { title: '方案对比', C: ComparePanel },
  verify: { title: '验算', C: VerifyPanel },
  results: { title: '实验结果', C: ExperimentPanel },
  decision: { title: '决策记录', C: DecisionPanel },
  replay: { title: '仿真回放', C: ReplayPanel },
  figures: { title: '科研图表', C: FiguresPanel },
  assets: { title: '资产库', C: AssetLibrary },
  assetPreview: { title: '资产预览', C: AssetPreview },
  assetInspector: { title: '资产检查器', C: AssetInspector },
  scenarios: { title: '场景库', C: ScenarioNavigator },
  algorithms: { title: '算法库', C: AlgorithmsPanel },
  cvrplib: { title: 'CVRPLIB', C: CvrplibPanel },
  fields: { title: '真实田块', C: FieldsPanel },
  references: { title: '文献', C: ReferencesPanel },
  code: { title: '源码', C: CodePanel },
  studies: { title: '课题', C: StudiesPanel },
  analysis: { title: '统计分析', C: AnalysisPanel },
  fieldsim: { title: '田间仿真', C: FieldSimPanel },
  physics: { title: '物理预演', C: PhysicsPanel },
  rosview: { title: 'ROS 视图', C: RosView },
  gazebo: { title: 'Gazebo 场景', C: GazeboView },
  telemetry: { title: '遥测', C: TelemetryView },
  sim: { title: 'TurtleBot4 仿真', C: SimPanel },
  runs: { title: '运行记录', C: RunsPanel },
  services: { title: '服务', C: ServicesPanel },
  console: { title: '日志', C: ConsolePanel },
  classic: { title: '经典视图', C: ClassicPanel },
  models: { title: '模型与用量', C: ModelsPage },
  capabilities: { title: '能力', C: CapabilitiesPage },
  data: { title: '数据', C: DataPage },
  desktop: { title: '桌面应用', C: DesktopPage },
  tools: { title: '工具与闸门', C: ToolsPage },
  evals: { title: '智能体评测', C: EvalsPage },
} as const;
export type PanelId = keyof typeof PANELS;

/** Panels that keep rendering while hidden (map editor state, WebGL contexts, live sockets). */
export const ALWAYS = new Set<PanelId>(['map', 'rosview', 'gazebo']);

export function PanelHost({ id }: { id: PanelId }) {
  const p = PANELS[id];
  return <ViewErrorBoundary name={p.title}><Suspense fallback={<div style={{ padding: 14 }}><Spinner /></div>}><p.C /></Suspense></ViewErrorBoundary>;
}
