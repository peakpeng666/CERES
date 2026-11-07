export interface Sourced { v: number; u?: string; r?: [number, number]; s: string; note?: string }
export interface Source { id: string; kind: string; title: string; url?: string; doi?: string; checked: 'document' | 'search_summary'; checked_on: string; scope: string }
export interface Environment { id: string; name: string; short: string; line_operations: string[]; spatial: string; structure: 'swath' | 'alley' | 'none'; turn_space: string; slope_deg: number; default_crops: string[]; platforms: string[]; note?: string }
export interface Crop { id: string; name: string; category: string; stand: 'row' | 'solid'; yield_t_ha: number; row_spacing_m: Sourced | null; row_width_m: Sourced | null; canopy_height_m: Sourced | null }
export interface Operation {
  id: string; name: string; family: 'line' | 'point' | 'cooperative' | 'support'; stage: string; resource: 'none' | 'consume' | 'collect';
  resource_name?: string; unit?: string; default_rate?: number; objects: string[]; crop_protected: boolean; speed?: Sourced; efficiency?: Sourced;
}
export interface Platform {
  id: string; name: string; class: string; use: string; hitch: string[]; tier: 'display' | 'planning' | 'simulation'; adapter?: string; pivot: boolean; reverse: boolean; ground: string; wheels: number; note?: string;
  length_m: Sourced; width_m: Sourced; track_m: Sourced; mass_kg: Sourced; payload_kg?: Sourced; lift_kg?: Sourced; pto_kw?: Sourced;
  speed_work_max_kmh: Sourced; speed_transport_kmh: Sourced; min_turn_radius_m: Sourced; endurance_h?: Sourced;
}
export interface Implement {
  id: string; name: string; category: string; hitch: string; trailed?: boolean; for?: string[]; operations: string[];
  width_m: Sourced; length_m: Sourced; mass_kg: Sourced; tank?: Sourced; speed_kmh?: Sourced; efficiency?: Sourced; turn_radius_m?: Sourced;
  draft?: { A: number; B: number; C: number; unit: string; depth_cm: number; F: number[]; s: string; note?: string }; pto?: { a: number; b: number; s: string };
}
export interface Catalog {
  schema: string; version: string; sources: Source[]; environments: Environment[]; crops: Crop[]; operations: Operation[]; platforms: Platform[]; implements: Implement[];
  labels: { tiers: Record<string, string>; basis: Record<string, string>; platform_classes: Record<string, string> };
}
export interface Mission {
  environment: string; crop: string | null; operation: string; platform: string; implement: string;
  field: { shape: string; width_m: number; height_m: number; obstacles?: number[][][]; ring?: number[][] };
  angle_deg: number; headland_m: number | null; fleet: number; objective: 'nonworking' | 'makespan' | null; station: string;
  overrides: { work_width_m?: number | null; capacity?: number | null; rate_per_ha?: number | null; speed_kmh?: number | null };
  title?: string;
}
export interface Check { id: string; level: 'pass' | 'warn' | 'block' | 'info'; title: string; detail: string; fix: string | null; sources: string[] }
export interface Line { id: number; row: number; piece: number; p1: number[]; p2: number[]; length_m: number; demand?: number }
export interface Rig {
  name: string; tier: string; work_width_m: number; clearance_width_m: number; length_m: number; track_m: number; min_turn_radius_m: number; pivot: boolean; reverse: boolean; trailed: boolean;
  speed_work_mps: number; speed_turn_mps: number; speed_transfer_mps: number; efficiency_typical: number | null; efficiency_range: number[] | null; endurance_s: number | null; mass_kg: number;
  resource: { kind: string; name?: string; unit?: string; capacity: number | null; rate_per_ha: number | null };
  derivation: Record<string, { v: number; u: string; rule: string; sources: string[] }>;
}
export interface Estimate {
  field_ha: number; work_ha: number; lines: number; line_total_m: number; pitch_m: number; theoretical_ha_h: number; effective_ha_h: number; hours_at_typical_efficiency: number | null;
  demand_total: number; fills_min: number | null; rows_per_fill: number | null; unit: string | null; turn_ratio: number; skip_rows_needed: number;
}
export interface Preview {
  checks: Check[]; blocked: boolean; estimate: Estimate | null; rig: Rig | null; lines: Line[]; field: number[][] | null; obstacles?: number[][][];
  stations?: { id: string; xy: number[]; service_s: number }[]; headland_m?: number; recommended_headland_m?: number; pitch_m?: number; pattern?: string; objective?: string; catalog_version?: string;
}
export interface Segment { kind: 'work' | 'turn' | 'transfer' | 'service'; polyline: number[][]; length_m: number; duration_s: number; turn?: string; reverse?: boolean; line?: number; station?: string }
