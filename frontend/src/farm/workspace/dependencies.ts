// Fork (printfarm): the settings OrcaSlicer greys out (toggle_field) or hides (toggle_line) while what they depend
// on is off, transcribed from OrcaSlicer v2.5.0: ConfigManipulation::toggle_print_fff_options (process) and
// TabFilament::toggle_options / TabPrinter::toggle_options (Tab.cpp). Rules run in the slicer's order and the
// last one about a key wins, as there. A rule that reads a key the project doesn't hold is skipped (nothing is
// greyed on a guess). Not transcribed: what depends on the printer model's own capabilities outside the project
// (wrapping detection, IMEX tool layouts) and the values the slicer resets by itself.
import type { SettingValue } from './api';

export interface Dependencies {
  /** greyed: the slicer keeps the field but disables it */
  disabled: Set<string>;
  /** hidden: the slicer removes the line */
  hidden: Set<string>;
}

class Missing extends Error {}

/** ``value``: a key's value now (its first element for a list); ``global``: the process window (not an object's). */
export function dependencies(value: (key: string) => SettingValue | undefined, global = true): Dependencies {
  const raw = (key: string): string => {
    const v = value(key);
    const first = Array.isArray(v) ? v[0] : v;
    if (first == null || first === 'nil') throw new Missing();
    return first;
  };
  const str = (k: string) => raw(k);
  const bool = (k: string) => ['1', 'true'].includes(raw(k).toLowerCase());
  const num = (k: string) => parseFloat(raw(k));
  const field = new Map<string, boolean>();
  const line = new Map<string, boolean>();
  // one toggle: skipped when its condition reads a key the project doesn't hold
  const toggle = (into: Map<string, boolean>, keys: string | string[], on: () => boolean) => {
    let result: boolean;
    try {
      result = on();
    } catch (e) {
      if (e instanceof Missing) return;
      throw e;
    }
    for (const k of typeof keys === 'string' ? [keys] : keys) into.set(k, result);
  };
  const f = (keys: string | string[], on: () => boolean) => toggle(field, keys, on);
  const l = (keys: string | string[], on: () => boolean) => toggle(line, keys, on);
  const safe = (on: () => boolean, fallback = false) => {
    try {
      return on();
    } catch (e) {
      if (e instanceof Missing) return fallback;
      throw e;
    }
  };

  // --- printer facts the process rules read (the printer preset is in the same project config)
  const gcodeFlavor = safe(() => !!str('gcode_flavor')) ? str('gcode_flavor') : ''; // '' when the file doesn't say
  const marlin2 = gcodeFlavor === 'marlin2';
  const klipper = gcodeFlavor === 'klipper';
  const belt = safe(() => bool('belt_printer'));
  const beltTilted =
    belt &&
    safe(() => ['X', 'Y', 'x', 'y'].includes(str('belt_slice_rotation'))) &&
    safe(() => Math.abs(num('belt_slice_rotation_angle')) >= 1 && Math.abs(num('belt_slice_rotation_angle')) <= 89);
  // a Bambu Lab printer (the rules reading it are skipped when the file doesn't name the printer model)
  const bbl = () => str('printer_model').startsWith('Bambu Lab');

  // ===================== process: ConfigManipulation::toggle_print_fff_options
  const slope = () => num('max_volumetric_extrusion_rate_slope') > 0;
  f('enable_arc_fitting', () => !slope() && !belt);
  l(['max_volumetric_extrusion_rate_slope_segment_length', 'extrusion_rate_smoothing_external_perimeter_only'], slope);

  const perimeters = () => num('wall_loops') > 0;
  f(
    ['extra_perimeters_on_overhangs', 'ensure_vertical_shell_thickness', 'detect_thin_wall', 'detect_overhang_wall', 'seam_position',
      'staggered_inner_seams', 'wall_sequence', 'outer_wall_line_width', 'inner_wall_speed', 'outer_wall_speed', 'small_perimeter_speed',
      'small_perimeter_threshold'],
    perimeters,
  );
  const infill = () => num('sparse_infill_density') > 0;
  l(
    ['sparse_infill_pattern', 'infill_combination', 'fill_multiline', 'infill_direction', 'infill_complete_top', 'minimum_sparse_infill_area',
      'sparse_infill_filament_id', 'infill_shift_step', 'sparse_infill_rotate_template', 'symmetric_infill_y_axis'],
    infill,
  );
  const pattern = () => str('sparse_infill_pattern');
  const anchor = () => infill() && !['concentric', 'spiralinset'].includes(pattern());
  l(['infill_anchor', 'infill_anchor_max'], anchor);
  l('infill_combination_max_layer_height', () => bool('infill_combination') && infill());
  const multilinePatterns = ['gyroid', 'grid', 'rectilinear', 'tpmsd', 'tpmsfk', 'crosshatch', 'honeycomb', 'lateral-lattice',
    'lateral-honeycomb', 'concentric', 'cubic', 'tri-hexagon', 'alignedrectilinear', 'lightning', '3dhoneycomb', 'adaptivecubic',
    'supportcubic', 'triangles', 'quartercubic', 'archimedeanchords', 'hilbertcurve', 'octagramspiral'];
  const adaptivePattern = () => infill() && num('sparse_infill_density') < 100 && ['gyroid', 'tpmsd', 'tpmsfk'].includes(pattern());
  l('adaptive_infill_mode', adaptivePattern);
  const adaptive = () => adaptivePattern() && str('adaptive_infill_mode') !== 'disabled';
  l(['adaptive_infill_interior_density', 'adaptive_infill_gradient'], adaptive);
  l('gyroid_optimized', () => infill() && pattern() === 'gyroid' && !adaptive());
  if (safe(infill)) {
    f('fill_multiline', () => multilinePatterns.includes(pattern()));
    f('infill_anchor_max', () => pattern() !== 'line');
    f('infill_anchor', () => pattern() !== 'line' && parseFloat(str('infill_anchor_max')) > 0);
  }
  l('infill_shift_step', () => ['crosszag', 'lockedzag'].includes(pattern()));
  l(['skeleton_infill_density', 'skin_infill_density', 'infill_lock_depth', 'skin_infill_depth', 'skin_infill_line_width',
    'skeleton_infill_line_width'], () => pattern() === 'lockedzag');
  l('symmetric_infill_y_axis', () => ['zigzag', 'crosszag', 'lockedzag'].includes(pattern()));
  l('infill_complete_top', () => infill() && pattern() === '3dhoneycomb');

  const spiral = () => bool('spiral_mode');
  l(['spiral_mode_smooth', 'spiral_starting_flow_ratio', 'spiral_finishing_flow_ratio'], spiral);
  l('spiral_mode_max_xy_smoothing', () => spiral() && bool('spiral_mode_smooth'));
  const topLayers = () => num('top_shell_layers') > 0 || (spiral() && num('bottom_shell_layers') > 1);
  const topShell = () => topLayers() && num('top_surface_density') > 0;
  const bottomShell = () => num('bottom_shell_layers') > 0;
  const solid = () => topLayers() || bottomShell();
  const smoothable = () => {
    const p = pattern();
    if (['hilbertcurve', 'octagramspiral', 'lightning', 'honeycomb', '3dhoneycomb', 'concentric', 'crosshatch'].includes(p)) return true;
    return ['grid', 'triangles', 'tri-hexagon', 'cubic'].includes(p) && num('fill_multiline') > 1;
  };
  l('sparse_infill_smooth_factor', () => infill() && smoothable());
  f(['top_surface_pattern', 'top_layer_direction'], topShell);
  f(['bottom_surface_pattern', 'bottom_surface_density', 'bottom_layer_direction'], bottomShell);
  f('top_surface_density', topLayers);
  l(['top_surface_expansion', 'top_surface_expansion_margin', 'top_surface_expansion_direction'], topShell);
  f(['top_surface_expansion_margin', 'top_surface_expansion_direction'], () => num('top_surface_expansion') > 0);
  const centered = (k: string) => ['archimedeanchords', 'octagramspiral'].includes(str(k));
  l('center_of_surface_pattern', () => (safe(topShell) && centered('top_surface_pattern')) || (safe(bottomShell) && centered('bottom_surface_pattern')));
  const separable = ['monotonic', 'monotonicline', 'rectilinear', 'alignedrectilinear', 'zigzag', 'crosszag', 'lockedzag', 'line', 'grid',
    'triangles', 'tri-hexagon', 'cubic', 'quartercubic', 'honeycomb', '3dhoneycomb', 'lateral-honeycomb', 'lateral-lattice', 'crosshatch',
    'tpmsd', 'tpmsfk', 'gyroid', 'hilbertcurve', 'archimedeanchords', 'octagramspiral'];
  l('separated_infills', () => separable.includes(pattern()));
  const centeredFill = (k: string) => ['concentric', 'spiralinset', 'archimedeanchords', 'octagramspiral'].includes(str(k));
  l('top_surface_fill_order', () => topShell() && centeredFill('top_surface_pattern'));
  l('bottom_surface_fill_order', () => bottomShell() && centeredFill('bottom_surface_pattern'));
  f(
    ['infill_direction', 'sparse_infill_line_width', 'gap_fill_target', 'filter_out_gap_fill', 'infill_wall_overlap', 'bridge_angle',
      'internal_bridge_angle', 'relative_bridge_angle', 'solid_infill_direction', 'solid_infill_rotate_template', 'internal_solid_infill_pattern',
      'internal_solid_filament_id', 'top_surface_filament_id', 'bottom_surface_filament_id', 'sparse_infill_speed', 'bridge_speed',
      'internal_bridge_speed'],
    () => safe(infill) || solid(),
  );
  f('top_shell_thickness', () => !spiral() && topLayers());
  f('bottom_shell_thickness', () => !spiral() && bottomShell());
  f('gap_infill_speed', perimeters);
  f(['top_surface_line_width', 'top_surface_speed'], topShell);
  f(
    ['outer_wall_acceleration', 'inner_wall_acceleration', 'initial_layer_acceleration', 'initial_layer_travel_acceleration',
      'top_surface_acceleration', 'travel_acceleration', 'bridge_acceleration', 'sparse_infill_acceleration', 'internal_solid_infill_acceleration'],
    () => num('default_acceleration') > 0,
  );
  const jerks = ['outer_wall_jerk', 'inner_wall_jerk', 'initial_layer_jerk', 'initial_layer_travel_jerk', 'top_surface_jerk', 'travel_jerk', 'infill_jerk'];
  const junction = marlin2 && safe(() => num('machine_max_junction_deviation') > 0);
  if (gcodeFlavor) {
    l('default_junction_deviation', () => marlin2);
    f('default_junction_deviation', () => junction);
    f('default_jerk', () => !junction);
    l(jerks, () => !junction);
  }
  if (!junction) f(jerks, () => num('default_jerk') > 0);

  const skirt = () => num('skirt_loops') > 0 && !belt;
  f('skirt_height', () => skirt() && str('draft_shield') !== 'enabled');
  l('single_loop_draft_shield', skirt);
  f(['skirt_type', 'min_skirt_length', 'skirt_distance', 'skirt_start_angle', 'skirt_speed', 'draft_shield'], skirt);
  if (belt) f(['skirt_loops', 'skirt_height'], () => false);

  const brim = () => str('brim_type') !== 'no_brim';
  f('brim_object_gap', brim);
  f(['brim_use_efc_outline', 'combine_brims'], () => brim() && !beltTilted);
  f('brim_width', () => (brim() && !['auto_brim', 'painted'].includes(str('brim_type'))) || (brim() && beltTilted));
  f('brim_flow_ratio', brim);
  l(['leading_brim_length', 'extra_brim_width'], () => beltTilted);
  f(['leading_brim_length', 'extra_brim_width'], () => bool('belt_printer') && beltTilted && brim());
  f(['outer_wall_filament_id', 'inner_wall_filament_id'], () => safe(perimeters) || brim());
  const autoEar = () => str('brim_type') === 'brim_ears' && !beltTilted;
  const paintedEar = () => str('brim_type') === 'painted' && !beltTilted;
  f(['brim_ears_max_angle', 'brim_ears_detection_length'], () => num('brim_width') > 0);
  f('brim_ears_outer_only', () => paintedEar() || num('brim_width') > 0);
  l(['brim_ears_max_angle', 'brim_ears_detection_length'], autoEar);
  l('brim_ears_outer_only', () => autoEar() || paintedEar());
  l('elefant_foot_compensation_layers', () => num('elefant_foot_compensation') > 0 || num('elefant_foot_layers_density') < 100);

  const raft = () => num('raft_layers') > 0 && !belt;
  if (belt) f('raft_layers', () => false);
  const support = () => bool('enable_support') || raft();
  const supportType = () => str('support_type');
  const isTree = () => ['tree(auto)', 'tree(manual)'].includes(supportType());
  const isAuto = () => ['normal(auto)', 'tree(auto)'].includes(supportType());
  const interfaceLayers = () => num('support_interface_top_layers') > 0 || num('support_interface_bottom_layers') > 0;
  const soluble = () => support() && num('support_top_z_distance') === 0;
  const style = () => str('support_style');
  f(
    ['support_style', 'support_base_pattern', 'support_base_pattern_spacing', 'support_expansion', 'support_angle', 'support_interface_pattern',
      'support_interface_top_layers', 'support_interface_bottom_layers', 'bridge_no_support', 'max_bridge_length', 'support_top_z_distance',
      'support_bottom_z_distance', 'support_type', 'support_on_build_plate_only', 'support_critical_regions_only', 'support_interface_not_for_body',
      'support_object_xy_distance', 'support_object_first_layer_gap', 'independent_support_layer_height'],
    support,
  );
  f('support_threshold_angle', () => support() && isAuto());
  f('support_threshold_overlap', () => num('support_threshold_angle') === 0 && support() && isAuto());
  const tree = () => bool('enable_support') && isTree();
  const organic = () => tree() && ['organic', 'default'].includes(style());
  const normalTree = () => tree() && !organic();
  l('support_threshold_overlap', () => !tree());
  l(['tree_support_branch_angle', 'tree_support_branch_distance', 'tree_support_branch_diameter', 'tree_support_auto_brim', 'tree_support_brim_width'], normalTree);
  l(['tree_support_branch_angle_organic', 'tree_support_branch_distance_organic', 'tree_support_branch_diameter_organic', 'tree_support_angle_slow',
    'tree_support_tip_diameter', 'tree_support_top_rate', 'tree_support_branch_diameter_angle'], organic);
  l('independent_support_layer_height', () => support() && !organic());
  f('tree_support_brim_width', () => tree() && !bool('tree_support_auto_brim'));
  l('max_bridge_length', tree);
  l('bridge_no_support', () => !tree());
  l('support_critical_regions_only', () => isAuto() && tree());
  f(['support_interface_filament', 'support_interface_loop_pattern', 'support_bottom_interface_spacing'], () => support() && interfaceLayers());
  const ironableSupport = () => raft() || (support() && num('support_interface_top_layers') > 0);
  f('support_ironing', ironableSupport);
  const supportIroning = () => ironableSupport() && bool('support_ironing');
  l(['support_ironing_pattern', 'support_ironing_flow', 'support_ironing_spacing'], supportIroning);
  f('support_interface_spacing', () => support() && interfaceLayers() && !supportIroning());
  f(['small_support_perimeter_speed', 'small_support_perimeter_threshold'], () => bool('enable_support'));
  f('inner_wall_line_width', () => safe(perimeters) || safe(skirt) || brim());
  f('support_filament', () => safe(support) || skirt());
  l('raft_contact_distance', () => raft() && !soluble());
  l('raft_expansion', raft);
  f('raft_expansion', () => !(isTree() && ['tree_slim', 'tree_strong', 'tree_hybrid'].includes(style())));
  f('raft_first_layer_density', support);
  f('raft_first_layer_expansion', () => support() && (!normalTree() || style() === 'tree_hybrid' || raft()));

  const ironing = () => str('ironing_type') !== 'no ironing';
  l(['ironing_pattern', 'ironing_flow', 'ironing_spacing', 'ironing_angle', 'ironing_inset', 'ironing_angle_fixed'], ironing);
  f(['ironing_angle', 'ironing_angle_fixed'], () => ironing() && str('ironing_pattern') === 'rectilinear');
  l('ironing_speed', () => safe(ironing) || supportIroning());
  l(['zaa_minimize_perimeter_height', 'zaa_min_z', 'zaa_dont_alternate_fill_direction', 'ironing_expansion'], () => bool('zaa_enabled'));
  f('print_order', () => str('print_sequence') !== 'by object');
  f('single_extruder_multi_material', () => !bbl());
  const semm = () => bool('single_extruder_multi_material');
  const wipeTower2 = () => !bbl() && str('wipe_tower_type') === 'type2';
  f('ooze_prevention', () => !semm());
  const ooze = () => bool('ooze_prevention');
  l(['standby_temperature_delta', 'preheat_time'], ooze);
  l('preheat_steps', () => ooze() && num('preheat_steps') > 0);
  const tower = () => bool('enable_prime_tower');
  l('belt_purge_tower_width', () => belt && bool('enable_belt_purge_tower'));
  l(['prime_tower_width', 'prime_tower_brim_width', 'prime_tower_skip_points', 'wipe_tower_wall_type', 'prime_tower_infill_gap',
    'prime_tower_enable_framework', 'enable_tower_interface_features'], () => tower() && !belt);
  l('enable_tower_interface_cooldown_during_tower', () => tower() && !belt && bool('enable_tower_interface_features'));
  l(['wipe_tower_rotation_angle', 'wipe_tower_cone_angle', 'wipe_tower_extra_spacing', 'wipe_tower_max_purge_speed', 'wipe_tower_bridging',
    'wipe_tower_extra_flow'], () => tower() && wipeTower2() && !belt);
  l('wipe_tower_no_sparse_layers', () => tower() && !belt);
  l('wipe_tower_sparse_layers_combination', () => tower() && !belt && !bool('wipe_tower_no_sparse_layers'));
  const rib = () => str('wipe_tower_wall_type') === 'rib' && tower() && !belt;
  l('wipe_tower_cone_angle', () => tower() && wipeTower2() && !belt && str('wipe_tower_wall_type') === 'cone');
  l(['wipe_tower_extra_rib_length', 'wipe_tower_rib_width', 'wipe_tower_fillet_wall'], rib);
  f('prime_tower_width', () => tower() && !safe(rib) && !belt);
  l('single_extruder_multi_material_priming', () => !semm() && tower() && wipeTower2() && !belt);
  l(['toolchange_cyclic_order', 'toolchange_cyclic_first_layer'], () => str('toolchange_ordering') === 'cyclic');
  l('prime_volume', () => tower() && (!bool('purge_in_prime_tower') || !semm()));
  f(['flush_into_infill', 'flush_into_support', 'flush_into_objects'], tower);
  l('max_travel_detour_distance', () => bool('reduce_crossing_wall'));
  l(['first_layer_flow_ratio', 'outer_wall_flow_ratio', 'inner_wall_flow_ratio', 'overhang_flow_ratio', 'sparse_infill_flow_ratio',
    'internal_solid_infill_flow_ratio', 'gap_fill_flow_ratio', 'support_flow_ratio', 'support_interface_flow_ratio'], () => bool('set_other_flow_ratios'));
  l(['overhang_1_4_speed', 'overhang_2_4_speed', 'overhang_3_4_speed', 'overhang_4_4_speed', 'slowdown_for_curled_perimeters'], () => bool('enable_overhang_speed'));
  l('flush_into_objects', () => !global);
  l('support_interface_not_for_body', () => num('support_interface_filament') !== 0 && num('support_filament') === 0);
  const fuzzy = () => str('fuzzy_skin') !== 'disabled_fuzzy'; // "none" is "Painted only"
  l(['fuzzy_skin_mode', 'fuzzy_skin_noise_type', 'fuzzy_skin_point_distance', 'fuzzy_skin_thickness', 'fuzzy_skin_first_layer'], fuzzy);
  const noise = () => str('fuzzy_skin_noise_type');
  l('fuzzy_skin_scale', () => noise() !== 'classic' && fuzzy() && noise() !== 'ripple');
  l('fuzzy_skin_octaves', () => !['classic', 'voronoi', 'ripple'].includes(noise()) && fuzzy());
  l('fuzzy_skin_persistence', () => ['perlin', 'billow'].includes(noise()) && fuzzy());
  l(['fuzzy_skin_ripples_per_layer', 'fuzzy_skin_ripple_offset', 'fuzzy_skin_layers_between_ripple_offset'], () => noise() === 'ripple' && fuzzy());
  const arachne = () => str('wall_generator') === 'arachne';
  l(['wall_transition_length', 'wall_transition_filter_deviation', 'wall_transition_angle', 'min_feature_size', 'min_length_factor', 'min_bead_width',
    'wall_distribution_count', 'initial_layer_min_bead_width', 'wall_maximum_resolution', 'wall_maximum_deviation'], arachne);
  f('detect_thin_wall', () => !arachne());
  f('wipe_speed', () => !bool('role_based_wipe_speed'));
  l('wipe_inward_distance', () => bool('wipe_inward'));
  if (gcodeFlavor) {
    l(['accel_to_decel_enable', 'accel_to_decel_factor'], () => klipper);
    if (klipper) f('accel_to_decel_factor', () => bool('accel_to_decel_enable'));
    l('exclude_object_tolerance', () => klipper && bool('exclude_object'));
  }
  l(['make_overhang_printable_angle', 'make_overhang_printable_hole_size'], () => bool('make_overhang_printable'));
  l('only_one_wall_first_layer', bottomShell);
  l('only_one_wall_top', topLayers);
  l('min_width_top_surface', () => (safe(topLayers) && safe(() => bool('only_one_wall_top'))) || (num('min_length_factor') > 0.5 && arachne()));
  l(['hole_to_polyhole_threshold', 'hole_to_polyhole_twisted', 'hole_to_polyhole_max_edges'], () => bool('hole_to_polyhole'));
  l('unsupported_wall_last', () => bool('detect_overhang_wall'));
  l('overhang_reverse', () => !spiral());
  l('overhang_reverse_internal_only', () => !spiral() && bool('overhang_reverse'));
  l('overhang_reverse_threshold', () => bool('detect_overhang_wall') && !spiral() && bool('overhang_reverse') && !bool('overhang_reverse_internal_only'));
  l('timelapse_type', bbl);
  l('small_area_infill_flow_compensation_model', () => bool('small_area_infill_flow_compensation'));
  f('seam_slope_type', () => !spiral() && !belt);
  const scarf = () => !spiral() && !belt && str('seam_slope_type') !== 'none';
  l(['seam_slope_conditional', 'seam_slope_start_height', 'seam_slope_entire_loop', 'seam_slope_min_length', 'seam_slope_steps',
    'seam_slope_inner_walls', 'scarf_joint_speed', 'scarf_joint_flow_ratio'], scarf);
  f('seam_slope_min_length', () => !bool('seam_slope_entire_loop'));
  l(['scarf_angle_threshold', 'scarf_overhang_threshold'], () => scarf() && bool('seam_slope_conditional'));
  const beam = () => bool('interlocking_beam');
  l('mmu_segmented_region_interlocking_depth', () => !beam());
  l(['interlocking_beam_width', 'interlocking_orientation', 'interlocking_beam_layer_count', 'interlocking_depth', 'interlocking_boundary_avoidance'], beam);
  l(['lateral_lattice_angle_1', 'lateral_lattice_angle_2'], () => pattern() === 'lateral-lattice');
  l(['lightning_overhang_angle', 'lightning_prune_angle', 'lightning_straightening_angle'], () => pattern() === 'lightning');
  const octree = () => ['adaptivecubic', 'supportcubic'].includes(pattern());
  f('sparse_infill_rotate_template', () => !octree());
  f('infill_direction', () => str('sparse_infill_rotate_template') === '' && !octree());
  f('solid_infill_direction', () => str('solid_infill_rotate_template') === '');
  l('infill_overhang_angle', () => pattern() === 'lateral-honeycomb');

  // ===================== filament: TabFilament::toggle_options
  f(['overhang_fan_speed', 'overhang_fan_threshold', 'internal_bridge_fan_speed'], () => bool('enable_overhang_bridge_fan'));
  f('dont_slow_down_outer_wall', () => bool('slow_down_for_layer_cooling'));
  l('initial_layer_fan_speed', () => num('close_fan_the_first_x_layers') <= 0);
  l('additional_cooling_fan_speed', () => bool('auxiliary_fan'));
  const filtration = () => bool('support_air_filtration');
  l(['activate_air_filtration', 'during_print_exhaust_fan_speed', 'complete_print_exhaust_fan_speed'], filtration);
  if (safe(filtration)) {
    f(['activate_air_filtration_during_print', 'activate_air_filtration_on_completion'], () => bool('activate_air_filtration'));
    f('during_print_exhaust_fan_speed', () => bool('activate_air_filtration') && bool('activate_air_filtration_during_print'));
    f('complete_print_exhaust_fan_speed', () => bool('activate_air_filtration') && bool('activate_air_filtration_on_completion'));
  }
  const pa = () => bool('enable_pressure_advance');
  f(['pressure_advance', 'adaptive_pressure_advance', 'adaptive_pressure_advance_overhangs'], pa);
  l(['adaptive_pressure_advance_overhangs', 'adaptive_pressure_advance_model', 'adaptive_pressure_advance_bridges'], () => bool('adaptive_pressure_advance') && pa());
  const pellet = () => bool('pellet_modded_printer');
  l('pellet_flow_coefficient', pellet);
  l('filament_diameter', () => !pellet());
  l('activate_chamber_temp_control', () => bool('support_chamber_temp_control'));
  f('filament_adaptive_volumetric_speed', () => str('volumetric_speed_coefficients') !== '0 0 0 0 0 0');
  f(['filament_minimal_purge_on_wipe_tower', 'filament_loading_speed_start', 'filament_loading_speed', 'filament_cooling_initial_speed',
    'filament_cooling_final_speed'], () => !bbl());
  f(['filament_multitool_ramming_volume', 'filament_multitool_ramming_flow'], () => bool('filament_multitool_ramming'));
  const extruders = () => {
    const v = value('nozzle_diameter');
    if (v == null) throw new Missing();
    return Array.isArray(v) ? v.length : 1;
  };
  l('long_retractions_when_ec', () => bbl() && extruders() > 1);
  l('retraction_distances_when_ec', () => bbl() && extruders() > 1 && bool('long_retractions_when_ec'));

  // ===================== printer: TabPrinter::toggle_options
  l(['scan_first_layer', 'bbl_calib_mark_logo', 'bbl_use_printhost'], bbl);
  l(['use_firmware_retraction', 'use_relative_e_distances', 'support_multi_bed_types', 'pellet_modded_printer', 'bed_mesh_max', 'bed_mesh_min',
    'bed_mesh_probe_distance', 'adaptive_bed_mesh_margin', 'thumbnails', 'thumbnails_format'], () => !bbl());
  l('enable_power_loss_recovery', () => bbl() || marlin2);
  f(['build_plate_tilt_x', 'build_plate_tilt_y'], () => !belt);
  l(['belt_printer_infinite_y', 'belt_slice_rotation_angle', 'belt_slice_rotation', 'gcode_remap_x', 'gcode_remap_y', 'gcode_remap_z',
    'belt_support_floor_offset'], () => belt);
  f('belt_slice_rotation_angle', () => bool('belt_printer') && !['None', 'none'].includes(str('belt_slice_rotation')));
  l('parallel_printheads_count', () => bool('support_parallel_printheads'));
  l('fan_direction', () => bool('auxiliary_fan'));
  l('support_air_filtration', () => !bool('support_cooling_filter'));
  l('cooling_filter_enabled', () => bool('support_cooling_filter'));
  l('enable_belt_purge_tower', () => belt);
  l('wipe_tower_type', () => !bbl() && !belt);
  f('extruders_count', () => !semm());
  f('manual_filament_change', semm);
  f('purge_in_prime_tower', () => semm() && wipeTower2());
  f(['imex_gantry_count', 'imex_tools_per_gantry', 'imex_carriage_margin', 'imex_tool_layout', 'imex_viz_theme', 'imex_firmware_managed_zones'],
    () => bool('is_imex'));
  f(['tool_change_on_wipe_tower', 'wait_for_temp_on_wipe_tower'], () => !semm() && wipeTower2() && extruders() > 1);
  // extruder page (retraction)
  const firmwareRetraction = () => bool('use_firmware_retraction');
  const retraction = () => num('retraction_length') > 0 || firmwareRetraction();
  f('retraction_minimum_travel', retraction);
  f(['z_hop', 'retract_when_changing_layer'], retraction);
  f(['retract_lift_above', 'retract_lift_below', 'retract_lift_enforce'], () => retraction() && num('z_hop') > 0);
  f(['retraction_speed', 'deretraction_speed', 'retract_before_wipe', 'retract_restart_extra', 'wipe_distance'],
    () => retraction() && !firmwareRetraction());
  const wipe = () => retraction() && bool('wipe');
  f('retract_before_wipe', () => wipe() && num('retract_after_wipe') !== 100);
  f('retract_after_wipe', () => wipe() && num('retract_before_wipe') !== 100);
  f('wipe_distance', wipe);
  f('retract_length_toolchange', () => extruders() > 1);
  f('retract_restart_extra_toolchange', () => extruders() > 1 && num('retract_length_toolchange') > 0);
  f('long_retractions_when_cut', () => !firmwareRetraction() && num('enable_long_retraction_when_cut') !== 0);
  l('retraction_distances_when_cut', () => bool('long_retractions_when_cut'));
  f('travel_slope', () => str('z_hop_types') !== 'Normal Lift');
  // motion ability
  const marlinLegacy = gcodeFlavor === 'marlin';
  const reprapFirmware = gcodeFlavor === 'reprapfirmware';
  if (gcodeFlavor) {
    f('machine_max_acceleration_travel', () => !marlinLegacy && !klipper);
    f('machine_max_junction_deviation', () => marlin2);
    f(['machine_max_jerk_x', 'machine_max_jerk_y', 'machine_max_jerk_z', 'machine_max_jerk_e'], () => !marlin2);
    f('emit_machine_limits_to_gcode', () => marlinLegacy || marlin2 || reprapFirmware);
    l(['input_shaping_emit', 'input_shaping_type', 'input_shaping_freq_x', 'input_shaping_freq_y', 'input_shaping_damp_x', 'input_shaping_damp_y'],
      () => marlin2 || reprapFirmware);
    f('input_shaping_emit', () => bool('emit_machine_limits_to_gcode'));
    const shaping = () => bool('emit_machine_limits_to_gcode') && bool('input_shaping_emit');
    f(['input_shaping_type', 'input_shaping_freq_x', 'input_shaping_damp_x'], shaping);
    f(['input_shaping_freq_y', 'input_shaping_damp_y'], () => shaping() && !reprapFirmware);
  }
  f(['min_resonance_avoidance_speed', 'max_resonance_avoidance_speed'], () => bool('resonance_avoidance'));

  const pick = (map: Map<string, boolean>) => new Set([...map].filter(([, on]) => !on).map(([k]) => k));
  return { disabled: pick(field), hidden: pick(line) };
}
