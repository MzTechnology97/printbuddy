// Fork (printfarm): OrcaSlicer's arrange options (GLCanvas3D::ArrangeSettings), kept per browser as Orca keeps them
// in its app config, and as the backend takes them (farm/workspace/arrange.py ArrangeOptions).

export interface ArrangeSettings {
  rotation: boolean;
  multiMaterial: boolean;
  avoidCalibration: boolean;
  alignY: boolean;
}

/** GLCanvas3D::ArrangeSettings' defaults; Reset turns "Align to Y axis" on for an i3 printer (printer_structure). */
export const arrangeDefaults = (i3: boolean): ArrangeSettings => ({ rotation: false, multiMaterial: true, avoidCalibration: false, alignY: i3 });

const KEY = 'pb.workspace.arrange';

export function loadArrangeSettings(i3: boolean): ArrangeSettings {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? 'null');
    return saved && typeof saved === 'object' ? { ...arrangeDefaults(i3), ...saved } : arrangeDefaults(i3);
  } catch {
    return arrangeDefaults(i3);
  }
}

export function saveArrangeSettings(s: ArrangeSettings) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* a private window: the options last the visit */
  }
}

/** The options as the backend takes them (arrange.py ArrangeOptions); "Align to Y axis" off while rotating. */
export const arrangeRequest = (s: ArrangeSettings, plate: number) => ({
  plate,
  allow_rotations: s.rotation,
  align_to_y_axis: s.alignY && !s.rotation,
  allow_multi_material: s.multiMaterial,
  avoid_calibration_region: s.avoidCalibration,
});
