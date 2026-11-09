// Fork (printfarm): settings OrcaSlicer greys out while the setting they depend on is off (no support -> the
// support options, no brim -> brim width...), from its ConfigManipulation::toggle_print_fff_options (v2.5.0).
// shortcut: the main parent switches only, not every rule of that function (printer kinds, belt printers,
// pattern-specific options); add a rule here when one is missed.
import type { SettingValue } from './api';

type Get = (key: string) => string | undefined;
interface Rule {
  /** On (true) or off: the keys are greyed while it is off. */
  on: (get: Get) => boolean;
  keys: string[];
}

const num = (v: string | undefined) => (v == null ? NaN : parseFloat(v));
const bool = (v: string | undefined) => v === '1' || v === 'true';
/** On unless the parent is in the project with the value that turns it off (a parent the project lacks: on). */
const unless = (key: string, off: (v: string) => boolean) => (get: Get) => {
  const v = get(key);
  return v == null || !off(v);
};

const SUPPORT = [
  'support_type', 'support_style', 'support_threshold_angle', 'support_threshold_overlap', 'support_on_build_plate_only',
  'support_critical_regions_only', 'support_base_pattern', 'support_base_pattern_spacing', 'support_expansion', 'support_angle',
  'support_interface_pattern', 'support_interface_top_layers', 'support_interface_bottom_layers', 'support_interface_spacing',
  'support_bottom_interface_spacing', 'support_interface_loop_pattern', 'support_interface_filament', 'support_interface_not_for_body',
  'support_top_z_distance', 'support_bottom_z_distance', 'support_object_xy_distance', 'support_object_first_layer_gap',
  'bridge_no_support', 'max_bridge_length', 'independent_support_layer_height', 'support_remove_small_overhang',
  'support_ironing', 'support_ironing_pattern', 'support_ironing_flow', 'support_ironing_spacing',
  'tree_support_branch_angle', 'tree_support_branch_distance', 'tree_support_branch_diameter', 'tree_support_auto_brim',
  'tree_support_brim_width', 'tree_support_branch_angle_organic', 'tree_support_branch_distance_organic',
  'tree_support_branch_diameter_organic', 'tree_support_angle_slow', 'tree_support_tip_diameter', 'tree_support_top_rate',
  'tree_support_branch_diameter_angle', 'raft_first_layer_density', 'small_support_perimeter_speed',
  'small_support_perimeter_threshold',
];

const RULES: Rule[] = [
  { on: (get) => get('enable_support') == null || bool(get('enable_support')) || num(get('raft_layers')) > 0, keys: SUPPORT },
  {
    on: unless('wall_loops', (v) => num(v) <= 0),
    keys: [
      'extra_perimeters_on_overhangs', 'ensure_vertical_shell_thickness', 'detect_thin_wall', 'detect_overhang_wall', 'seam_position',
      'staggered_inner_seams', 'wall_sequence', 'outer_wall_line_width', 'inner_wall_speed', 'outer_wall_speed', 'small_perimeter_speed',
      'small_perimeter_threshold', 'gap_infill_speed',
    ],
  },
  {
    on: unless('sparse_infill_density', (v) => num(v) <= 0),
    keys: [
      'sparse_infill_pattern', 'infill_combination', 'fill_multiline', 'infill_anchor', 'infill_anchor_max',
      'infill_combination_max_layer_height', 'minimum_sparse_infill_area', 'sparse_infill_filament_id', 'sparse_infill_rotate_template',
      'gyroid_optimized', 'adaptive_infill_mode',
    ],
  },
  { on: unless('infill_combination', (v) => !bool(v)), keys: ['infill_combination_max_layer_height'] },
  {
    on: unless('top_shell_layers', (v) => num(v) <= 0),
    keys: ['top_surface_pattern', 'top_surface_density', 'top_layer_direction', 'top_shell_thickness', 'top_surface_line_width', 'top_surface_speed', 'only_one_wall_top'],
  },
  {
    on: unless('bottom_shell_layers', (v) => num(v) <= 0),
    keys: ['bottom_surface_pattern', 'bottom_surface_density', 'bottom_layer_direction', 'bottom_shell_thickness', 'only_one_wall_first_layer'],
  },
  {
    on: unless('spiral_mode', (v) => !bool(v)),
    keys: ['spiral_mode_smooth', 'spiral_mode_max_xy_smoothing', 'spiral_starting_flow_ratio', 'spiral_finishing_flow_ratio'],
  },
  {
    on: unless('default_acceleration', (v) => num(v) <= 0),
    keys: [
      'outer_wall_acceleration', 'inner_wall_acceleration', 'initial_layer_acceleration', 'initial_layer_travel_acceleration',
      'top_surface_acceleration', 'travel_acceleration', 'bridge_acceleration', 'sparse_infill_acceleration', 'internal_solid_infill_acceleration',
    ],
  },
  {
    on: unless('default_jerk', (v) => num(v) <= 0),
    keys: ['outer_wall_jerk', 'inner_wall_jerk', 'initial_layer_jerk', 'initial_layer_travel_jerk', 'top_surface_jerk', 'travel_jerk', 'infill_jerk'],
  },
  {
    on: unless('skirt_loops', (v) => num(v) <= 0),
    keys: ['skirt_type', 'min_skirt_length', 'skirt_distance', 'skirt_start_angle', 'skirt_speed', 'draft_shield', 'skirt_height', 'single_loop_draft_shield'],
  },
  {
    on: unless('brim_type', (v) => v === 'no_brim'),
    keys: ['brim_object_gap', 'brim_width', 'brim_flow_ratio', 'brim_use_efc_outline', 'combine_brims', 'brim_ears_max_angle', 'brim_ears_detection_length'],
  },
  // an automatic or painted brim has no width to set
  { on: unless('brim_type', (v) => v === 'auto_brim' || v === 'painted'), keys: ['brim_width'] },
  {
    on: unless('ironing_type', (v) => v === 'no ironing'),
    keys: ['ironing_pattern', 'ironing_flow', 'ironing_spacing', 'ironing_angle', 'ironing_inset', 'ironing_angle_fixed', 'ironing_speed'],
  },
  { on: unless('ooze_prevention', (v) => !bool(v)), keys: ['standby_temperature_delta', 'preheat_time', 'preheat_steps'] },
  {
    on: unless('enable_prime_tower', (v) => !bool(v)),
    keys: [
      'prime_tower_width', 'prime_tower_brim_width', 'prime_tower_skip_points', 'wipe_tower_wall_type', 'prime_tower_infill_gap',
      'prime_tower_enable_framework', 'enable_tower_interface_features', 'wipe_tower_rotation_angle', 'wipe_tower_cone_angle',
      'wipe_tower_extra_spacing', 'wipe_tower_max_purge_speed', 'wipe_tower_bridging', 'wipe_tower_extra_flow', 'wipe_tower_no_sparse_layers',
    ],
  },
  {
    on: unless('set_other_flow_ratios', (v) => !bool(v)),
    keys: [
      'first_layer_flow_ratio', 'outer_wall_flow_ratio', 'inner_wall_flow_ratio', 'overhang_flow_ratio', 'sparse_infill_flow_ratio',
      'internal_solid_infill_flow_ratio', 'gap_fill_flow_ratio', 'support_flow_ratio', 'support_interface_flow_ratio',
    ],
  },
  {
    on: unless('enable_overhang_speed', (v) => !bool(v)),
    keys: ['overhang_1_4_speed', 'overhang_2_4_speed', 'overhang_3_4_speed', 'overhang_4_4_speed', 'slowdown_for_curled_perimeters'],
  },
  {
    on: unless('fuzzy_skin', (v) => v === 'disabled_fuzzy'), // "none" is "Painted only" in OrcaSlicer 2.5
    keys: ['fuzzy_skin_mode', 'fuzzy_skin_noise_type', 'fuzzy_skin_point_distance', 'fuzzy_skin_thickness', 'fuzzy_skin_first_layer'],
  },
  {
    on: unless('wall_generator', (v) => v !== 'arachne'),
    keys: [
      'wall_transition_length', 'wall_transition_filter_deviation', 'wall_transition_angle', 'min_feature_size', 'min_length_factor',
      'min_bead_width', 'wall_distribution_count', 'initial_layer_min_bead_width', 'wall_maximum_resolution', 'wall_maximum_deviation',
    ],
  },
  { on: unless('make_overhang_printable', (v) => !bool(v)), keys: ['make_overhang_printable_angle', 'make_overhang_printable_hole_size'] },
  {
    on: unless('seam_slope_type', (v) => v === 'none'),
    keys: [
      'seam_slope_conditional', 'seam_slope_start_height', 'seam_slope_entire_loop', 'seam_slope_min_length', 'seam_slope_steps',
      'seam_slope_inner_walls', 'scarf_joint_speed', 'scarf_joint_flow_ratio', 'scarf_angle_threshold', 'scarf_overhang_threshold',
    ],
  },
  {
    on: unless('interlocking_beam', (v) => !bool(v)),
    keys: ['interlocking_beam_width', 'interlocking_orientation', 'interlocking_beam_layer_count', 'interlocking_depth', 'interlocking_boundary_avoidance'],
  },
];

/** The keys greyed out with these values (a list value: its first element, as OrcaSlicer's first variant). */
export function greyedKeys(value: (key: string) => SettingValue | undefined): Set<string> {
  const get: Get = (key) => {
    const v = value(key);
    return Array.isArray(v) ? v[0] : v;
  };
  const out = new Set<string>();
  for (const rule of RULES) if (!rule.on(get)) rule.keys.forEach((k) => out.add(k));
  return out;
}
