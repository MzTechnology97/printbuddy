// Fork (printfarm): the consistency warnings OrcaSlicer shows while settings are edited (v2.5.0:
// ConfigManipulation::update_print_fff_config and its check_* functions, Print::validate's layer height check).
// The slicer corrects some of these by itself; here a warning is shown and the value is left to the user, so
// nothing changes behind their back. Each warning names the settings it is about.
import type { Section } from './orcaLayout';
import type { SettingValue } from './api';

export interface ConsistencyWarning {
  /** farm.workspace.consistency.<id> */
  id: string;
  /** the settings it is about (marked in their rows) */
  keys: string[];
  section: Section;
  /** values for the message */
  values?: Record<string, string | number>;
}

class Missing extends Error {}

/** The warnings for these values (``value``: a key's value now). A rule reading a key the file lacks is skipped. */
export function consistencyWarnings(value: (key: string) => SettingValue | undefined): ConsistencyWarning[] {
  const list = (key: string): string[] => {
    const v = value(key);
    if (v == null) throw new Missing();
    return Array.isArray(v) ? v : [v];
  };
  const str = (k: string) => {
    const v = list(k)[0];
    if (v == null || v === 'nil') throw new Missing();
    return v;
  };
  const num = (k: string) => parseFloat(str(k));
  const bool = (k: string) => ['1', 'true'].includes(str(k).toLowerCase());
  const out: ConsistencyWarning[] = [];
  const warn = (rule: () => ConsistencyWarning | false | null | undefined) => {
    try {
      const result = rule();
      if (result) out.push(result);
    } catch (e) {
      if (!(e instanceof Missing)) throw e;
    }
  };
  const process = (id: string, keys: string[], values?: ConsistencyWarning['values']): ConsistencyWarning => ({ id, keys, section: 'process', values });
  const filaments = () => list('filament_colour').length;

  warn(() => num('layer_height') <= 0 && process('layerZero', ['layer_height']));
  warn(() => num('initial_layer_print_height') <= 0 && process('firstLayerZero', ['initial_layer_print_height']));
  warn(() => {
    const h = num('layer_height');
    const min = num('min_layer_height');
    const max = num('max_layer_height');
    return h > 0 && ((max > 0 && h > max + 1e-6) || (min > 0 && h < min - 1e-6)) && process('layerLimits', ['layer_height'], { min, max });
  });
  warn(() => num('layer_height') > num('nozzle_diameter') && process('layerNozzle', ['layer_height'], { nozzle: num('nozzle_diameter') }));
  warn(() => num('ironing_spacing') < 0.05 && process('ironingSpacing', ['ironing_spacing']));
  warn(() => num('support_ironing_spacing') < 0.05 && process('ironingSpacing', ['support_ironing_spacing']));
  warn(() => Math.abs(num('xy_hole_compensation')) > 2 && process('xyCompensation', ['xy_hole_compensation']));
  warn(() => Math.abs(num('xy_contour_compensation')) > 2 && process('xyCompensation', ['xy_contour_compensation']));
  warn(() => num('elefant_foot_compensation') > 1 && process('elephantFoot', ['elefant_foot_compensation']));
  warn(() => {
    if (!bool('spiral_mode')) return false;
    const ready =
      num('wall_loops') === 1 &&
      num('top_shell_layers') === 0 &&
      num('sparse_infill_density') === 0 &&
      !bool('enable_support') &&
      num('enforce_support_layers') === 0 &&
      !bool('detect_thin_wall') &&
      !bool('overhang_reverse') &&
      str('timelapse_type') === '0' &&
      !(value('enable_wrapping_detection') != null && bool('enable_wrapping_detection'));
    return (
      !ready &&
      process('spiral', ['spiral_mode', 'wall_loops', 'top_shell_layers', 'sparse_infill_density', 'enable_support', 'enforce_support_layers',
        'detect_thin_wall', 'overhang_reverse', 'timelapse_type'])
    );
  });
  warn(() => bool('alternate_extra_wall') && str('ensure_vertical_shell_thickness') === 'ensure_all' &&
    process('alternateWall', ['alternate_extra_wall', 'ensure_vertical_shell_thickness']));
  const tower = () => filaments() > 1 && bool('enable_prime_tower');
  warn(() => tower() && (bool('adaptive_layer_height') || bool('independent_support_layer_height')) &&
    process('towerLayers', ['enable_prime_tower', 'adaptive_layer_height', 'independent_support_layer_height']));
  warn(() => {
    if (!tower() || !bool('enable_support') || bool('independent_support_layer_height')) return false;
    const gap = num('support_top_z_distance');
    const h = num('layer_height');
    return h > 0 && Math.abs(Math.round(gap / h) * h - gap) > 1e-6 && process('towerSupportGap', ['support_top_z_distance', 'layer_height']);
  });
  warn(() => bool('enable_prime_tower') && str('printer_model').startsWith('Bambu Lab') && str('wipe_tower_wall_type') === 'cone' &&
    process('towerCone', ['wipe_tower_wall_type']));
  warn(() => bool('enable_support') && !bool('detect_overhang_wall') && process('supportOverhangs', ['enable_support', 'detect_overhang_wall']));
  warn(() => {
    if (!bool('enable_support')) return false;
    const tree = ['tree(auto)', 'tree(manual)'].includes(str('support_type'));
    const allowed = tree ? ['default', 'tree_slim', 'tree_strong', 'tree_hybrid', 'organic'] : ['default', 'grid', 'snug'];
    return !allowed.includes(str('support_style')) && process('supportStyle', ['support_style', 'support_type']);
  });
  for (const key of ['support_filament', 'support_interface_filament', 'wipe_tower_filament', 'outer_wall_filament_id', 'inner_wall_filament_id',
    'sparse_infill_filament_id', 'internal_solid_filament_id', 'top_surface_filament_id', 'bottom_surface_filament_id']) {
    warn(() => num(key) > filaments() && process('filamentIndex', [key], { count: filaments() }));
  }
  warn(() => {
    if (str('seam_slope_type') === 'none') return false;
    const start = str('seam_slope_start_height');
    const h = num('layer_height');
    const height = start.endsWith('%') ? (parseFloat(start) / 100) * h : parseFloat(start);
    return height >= h && process('seamSlope', ['seam_slope_start_height', 'layer_height']);
  });
  warn(() => num('infill_lock_depth') > num('skin_infill_depth') && process('lockDepth', ['infill_lock_depth', 'skin_infill_depth']));
  warn(() => str('fuzzy_skin') !== 'disabled_fuzzy' && str('fuzzy_skin_mode') !== 'displacement' && str('wall_generator') !== 'arachne' &&
    process('fuzzyArachne', ['fuzzy_skin_mode', 'wall_generator']));
  warn(() => bool('belt_printer') && (num('raft_layers') > 0 || str('draft_shield') !== 'disabled') &&
    process('beltRaft', ['raft_layers', 'draft_shield']));

  // filament (the first filament out of range is named)
  const temperatures = (key: string) =>
    warn(() => {
      const values = list(key);
      const lows = list('nozzle_temperature_range_low');
      const highs = list('nozzle_temperature_range_high');
      const i = values.findIndex((v, n) => {
        const t = parseFloat(v);
        const low = parseFloat(lows[n] ?? lows[0]);
        const high = parseFloat(highs[n] ?? highs[0]);
        return Number.isFinite(t) && Number.isFinite(low) && Number.isFinite(high) && (t < low || t > high);
      });
      return i >= 0 && { id: 'nozzleTemperature', keys: [key], section: 'filament', values: { n: i + 1, low: lows[i] ?? lows[0], high: highs[i] ?? highs[0] } };
    });
  temperatures('nozzle_temperature');
  temperatures('nozzle_temperature_initial_layer');
  warn(() => {
    const lows = list('nozzle_temperature_range_low');
    const highs = list('nozzle_temperature_range_high');
    const i = lows.findIndex((l, n) => parseFloat(l) > parseFloat(highs[n] ?? highs[0]));
    return i >= 0 && { id: 'temperatureRange', keys: ['nozzle_temperature_range_low', 'nozzle_temperature_range_high'], section: 'filament', values: { n: i + 1 } };
  });
  warn(() => {
    const i = list('filament_max_volumetric_speed').findIndex((v) => parseFloat(v) < 0.5);
    return i >= 0 && { id: 'volumetricSpeed', keys: ['filament_max_volumetric_speed'], section: 'filament', values: { n: i + 1 } };
  });
  warn(() =>
    num('chamber_minimal_temperature') > num('chamber_temperature') && {
      id: 'chamberMinimal',
      keys: ['chamber_minimal_temperature', 'chamber_temperature'],
      section: 'filament',
      values: { min: num('chamber_minimal_temperature'), target: num('chamber_temperature') },
    },
  );
  return out;
}
