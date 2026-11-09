// G-code coloured by feature type (outer wall, infill, support...), as a slicer's preview shows it.
// OrcaSlicer and Bambu Studio mark each block with "; FEATURE: <name>" (PrusaSlicer: ";TYPE:<name>").
// gcode-preview colours by tool and parses T0-T7 only, so the features are folded into 8 groups and each
// block is switched to the "tool" of its group; the real tool changes are commented out.

export const FEATURE_GROUPS = [
  'outerWall',
  'innerWall',
  'sparseInfill',
  'solidInfill',
  'topSurface',
  'bridge',
  'support',
  'other',
] as const;
export type FeatureGroup = (typeof FEATURE_GROUPS)[number];

/** One colour per group (Printbuddy's palette). */
export const FEATURE_COLORS = ['#f2994a', '#f2d16b', '#c0392b', '#8e5cc2', '#e84a5f', '#3d8fd1', '#2bb38b', '#9aa3ad'];

const FEATURE_RE = /^\s*;\s*(?:FEATURE|TYPE)\s*:\s*(.+?)\s*$/i;
const TOOL_RE = /^\s*T\d+\b/i;

/** The group of a slicer's feature name (case and wording of OrcaSlicer, Bambu Studio and PrusaSlicer). */
export function featureGroup(name: string): number {
  const n = name.toLowerCase();
  if (n.includes('support')) return 6;
  if (n.includes('bridge')) return 5;
  if (n.includes('top') || n.includes('ironing')) return 4;
  if (n.includes('solid') || n.includes('bottom') || n.includes('gap')) return 3;
  if (n.includes('infill')) return 2;
  if (n.includes('outer') || n.includes('external')) return 0;
  if (n.includes('wall') || n.includes('perimeter')) return 1;
  return 7;
}

/** ``gcode`` with each feature block on its group's tool, and the groups it holds (in group order). */
export function colorByFeature(gcode: string): { gcode: string; groups: number[] } {
  const seen = new Set<number>();
  const out: string[] = ['T7'];
  for (const line of gcode.split('\n')) {
    const feature = line.match(FEATURE_RE);
    if (feature) {
      const group = featureGroup(feature[1]);
      seen.add(group);
      out.push(line, `T${group}`);
    } else if (TOOL_RE.test(line)) {
      out.push(`; tool change shown by feature: ${line.trim()}`);
    } else {
      out.push(line);
    }
  }
  return { gcode: out.join('\n'), groups: [...seen].sort((a, b) => a - b) };
}
