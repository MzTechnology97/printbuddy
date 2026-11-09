/** Fork (printfarm): G-code coloured by feature type for the workspace preview. */
import { describe, it, expect } from 'vitest';
import { FEATURE_GROUPS, colorByFeature, featureGroup } from '../../utils/gcodeFeatures';

const group = (name: string) => FEATURE_GROUPS[featureGroup(name)];

describe('featureGroup', () => {
  it('folds the slicers feature names into the legend groups', () => {
    // OrcaSlicer / Bambu Studio
    expect(group('Outer wall')).toBe('outerWall');
    expect(group('Inner wall')).toBe('innerWall');
    expect(group('Overhang wall')).toBe('innerWall');
    expect(group('Sparse infill')).toBe('sparseInfill');
    expect(group('Internal solid infill')).toBe('solidInfill');
    expect(group('Bottom surface')).toBe('solidInfill');
    expect(group('Gap infill')).toBe('solidInfill');
    expect(group('Top surface')).toBe('topSurface');
    expect(group('Ironing')).toBe('topSurface');
    expect(group('Bridge')).toBe('bridge');
    expect(group('Internal Bridge')).toBe('bridge');
    expect(group('Support interface')).toBe('support');
    expect(group('Skirt')).toBe('other');
    expect(group('Prime tower')).toBe('other');
    // PrusaSlicer wording
    expect(group('External perimeter')).toBe('outerWall');
    expect(group('Perimeter')).toBe('innerWall');
    expect(group('Top solid infill')).toBe('topSurface');
    expect(group('Support material')).toBe('support');
  });
});

describe('colorByFeature', () => {
  it('switches each feature block to its group and drops the real tool changes', () => {
    const { gcode, groups } = colorByFeature(
      ['G28', 'T1', '; FEATURE: Outer wall', 'G1 X1 E1', ';TYPE:Sparse infill', 'G1 X2 E2', 'T0', 'M84'].join('\n'),
    );
    expect(groups).toEqual([0, 2]);
    expect(gcode.split('\n')).toEqual([
      'T7',
      'G28',
      '; tool change shown by feature: T1',
      '; FEATURE: Outer wall',
      'T0',
      'G1 X1 E1',
      ';TYPE:Sparse infill',
      'T2',
      'G1 X2 E2',
      '; tool change shown by feature: T0',
      'M84',
    ]);
  });
});
