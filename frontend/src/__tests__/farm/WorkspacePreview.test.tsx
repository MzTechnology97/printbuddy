/**
 * Printfarm (fork): the 3MF Workspace's Preview. The G-code read as OrcaSlicer's GCodeProcessor reads it (move
 * types, roles, layers, widths, seams, filament per use, the slicer's times), the legend's colour scales and
 * number formats, and the legend panel.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import { render } from '../utils';
import { MOVE, ROLE, parseDuration, parseGcode } from '../../farm/workspace/gcode/parse';
import {
  ColorRange,
  RANGE_COLORS,
  colorFunction,
  compactCount,
  compactWeight,
  defaultViewType,
  distance,
  percent,
  rangeOf,
  shortTime,
} from '../../farm/workspace/gcode/legend';
import { LegendPanel } from '../../farm/workspace/gcode/LegendPanel';
import { initialSliders, sliderReducer } from '../../farm/workspace/gcode/sliders';

// An OrcaSlicer-like plate: header and config, two layers, a closed outer wall (a seam), travel, retract,
// wipe, a filament change with flushing, an arc, and the footer's filament totals.
const GCODE = [
  '; HEADER_BLOCK_START',
  '; model printing time: 9m 0s; total estimated time: 10m 0s',
  '; filament_density: 1.25,1.25',
  '; filament_diameter: 1.75,1.75',
  '; HEADER_BLOCK_END',
  '; CONFIG_BLOCK_START',
  '; filament_colour = #FF0000;#00FF00',
  '; default_acceleration = 1000',
  '; nozzle_diameter = 0.4',
  '; CONFIG_BLOCK_END',
  'G90',
  'M83',
  '; FEATURE: Custom',
  'G1 X0 Y0 Z0.2 F6000',
  '; CHANGE_LAYER',
  '; Z_HEIGHT: 0.2',
  '; LAYER_HEIGHT: 0.2',
  'M106 P1 S255',
  'M106 P2 S100',
  'M104 S215',
  '; FEATURE: Outer wall',
  '; LINE_WIDTH: 0.42',
  'G1 X10 Y0 E0.5 F1800',
  'G1 X10 Y10 E0.5',
  'G1 X0 Y10 E0.5',
  'G1 X0 Y0 E0.5',
  '; FEATURE: Sparse infill',
  '; LINE_WIDTH: 0.45',
  'G1 X5 Y5 E0.3',
  'G1 E-0.8 F1800',
  '; WIPE_START',
  'G1 X6 Y6',
  '; WIPE_END',
  'G1 X20 Y20 F12000',
  'G1 E0.8 F1800',
  'G2 X30 Y20 I5 J0 E1',
  '; CHANGE_LAYER',
  '; Z_HEIGHT: 0.4',
  '; LAYER_HEIGHT: 0.2',
  'G1 Z0.4',
  'T1',
  '; FLUSH_START',
  'G1 E10 F300',
  '; FLUSH_END',
  '; FEATURE: Support',
  'M204 S2000',
  'G1 X40 Y20 E0.4 F3000',
  '; filament used [mm] = 100.00, 50.00',
  '; filament used [g] = 0.30, 0.15',
  '; filament cost = 0.01, 0.02',
].join('\n');

describe('parseGcode', () => {
  const tp = parseGcode(GCODE);
  const of = (type: number) => [...tp.type].map((t, i) => (t === type ? i : -1)).filter((i) => i >= 0);

  it('reads layers, roles, widths, heights, fan and temperature as GCodeProcessor does', () => {
    expect(tp.layerStart.length - 1).toBe(2);
    expect(tp.layerZ).toEqual([0.2, 0.4]);
    const walls = of(MOVE.extrude).filter((i) => tp.role[i] === ROLE['Outer wall']);
    expect(walls).toHaveLength(4);
    expect(tp.width[walls[0]]).toBeCloseTo(0.42);
    expect(tp.height[walls[0]]).toBeCloseTo(0.2);
    expect(tp.fan[walls[0]]).toBeCloseTo(100); // M106 P1 is the part fan on Bambu printers, P2 is not
    expect(tp.temperature[walls[0]]).toBe(215);
    expect(tp.feedrate[walls[0]]).toBe(30);
    expect(tp.layer[walls[0]]).toBe(0);
  });

  it('classifies travels, retractions, wipes and unretractions, and marks the closed outer wall as a seam', () => {
    expect(of(MOVE.retract)).toHaveLength(1);
    expect(of(MOVE.wipe)).toHaveLength(1);
    expect(of(MOVE.unretract)).toHaveLength(2); // after the wipe, and the flush
    const seams = of(MOVE.seam);
    expect(seams).toHaveLength(1);
    expect([tp.x[seams[0]], tp.y[seams[0]]]).toEqual([0, 0]);
    expect(tp.stats.typeCount[MOVE.travel]).toBeGreaterThan(0);
  });

  it('splits an arc into chords and keeps its end point', () => {
    const arc = of(MOVE.extrude).filter((i) => tp.line[i] === GCODE.split('\n').indexOf('G2 X30 Y20 I5 J0 E1'));
    expect(arc.length).toBeGreaterThan(5);
    const last = arc[arc.length - 1];
    expect([tp.x[last], tp.y[last]]).toEqual([30, 20]);
    expect(Math.max(...arc.map((i) => tp.y[i]))).toBeGreaterThan(24.9); // G2 is clockwise: from the left point over the top
  });

  it('counts filament per use, a filament change, and takes the slicer totals and times', () => {
    const s = tp.stats;
    expect(s.filamentChanges).toBe(1);
    expect(s.extruderChanges).toBe(0); // one nozzle
    expect(s.usedExtruders).toEqual([0, 1]);
    const area = Math.PI * 0.875 ** 2;
    expect(s.flushMm3[1]).toBeCloseTo(area * 10);
    expect(s.supportMm3[1]).toBeCloseTo(area * 0.4);
    expect(s.usedG).toEqual([0.3, 0.15]);
    expect(s.cost).toEqual([0.01, 0.02]);
    expect(s.totalTime).toBe(600);
    expect(s.prepareTime).toBe(60);
    const sum = [...tp.time].reduce((a, b) => a + b, 0);
    expect(sum).toBeCloseTo(600, 3); // scaled to the slicer's estimate
    expect(tp.acceleration[of(MOVE.extrude).at(-1)!]).toBe(2000);
    expect(tp.config.filament_colour).toBe('#FF0000;#00FF00');
  });

  it('reads durations as the header writes them', () => {
    expect(parseDuration('1d 2h 3m 4s')).toBe(93784);
    expect(parseDuration(' 4h 29m 38s')).toBe(16178);
  });

  it('opens on Filament for a multi-material plate, else Line Type (apply_default_view_type)', () => {
    expect(defaultViewType(tp)).toBe('filament');
    expect(defaultViewType(parseGcode('; CHANGE_LAYER\n; FEATURE: Outer wall\nG1 X1 Y1 Z0.2 E1 F600'))).toBe('feature');
  });
});

describe('legend', () => {
  it('formats as Orca (short_time, format_compact_weight, format_distance, format_compact_count, percent)', () => {
    expect(shortTime(16553)).toBe('4h36m');
    expect(shortTime(265)).toBe('4m25s');
    expect(shortTime(12.4)).toBe('12s');
    expect(shortTime(0.4)).toBe('<1s');
    expect(shortTime(0)).toBe('0s');
    expect(shortTime(90061)).toBe('1d1h1m');
    expect(compactWeight(58.5)).toBe('58.50g');
    expect(compactWeight(1250)).toBe('1.25kg');
    expect(distance(850.4)).toBe('850mm');
    expect(distance(203277)).toBe('203.28m');
    expect(compactCount(950)).toBe('950');
    expect(compactCount(9932)).toBe('9.9K');
    expect(percent(0)).toBe('0');
    expect(percent(0.0005)).toBe('<0.1');
    expect(percent(0.201)).toBe('20.1');
  });

  it('lists a colour range as libvgcode does: one value, two, or the 11 steps from the top', () => {
    const one = new ColorRange();
    one.update(0.2);
    one.update(0.2);
    expect(one.rows()).toEqual([{ color: RANGE_COLORS[0], value: 0.2 }]);
    const two = new ColorRange();
    two.update(0.2);
    two.update(0.4);
    expect(two.rows().map((r) => r.value)).toEqual([0.4, 0.2]);
    const many = new ColorRange();
    [10, 20, 60, 110].forEach((v) => many.update(v));
    const rows = many.rows();
    expect(rows).toHaveLength(11);
    expect(rows[0]).toEqual({ color: RANGE_COLORS[10], value: 110 });
    expect(rows[10]).toEqual({ color: RANGE_COLORS[0], value: 10 });
    expect(many.color(10)).toEqual(RANGE_COLORS[0]);
    expect(many.color(110)).toEqual(RANGE_COLORS[10]);
    const log = new ColorRange(true);
    [1, 10, 100].forEach((v) => log.update(v));
    expect(log.rows()[5].value).toBeCloseTo(10);
  });

  it('ranges over the moves a view colours: travels count for speed only while shown', () => {
    const tp = parseGcode(GCODE);
    const all = Array(20).fill(true);
    const hidden = { travel: false, retract: false, unretract: false, wipe: false, seam: true };
    expect(rangeOf(tp, 'speed', all, hidden).max).toBe(50);
    expect(rangeOf(tp, 'speed', all, { ...hidden, travel: true }).max).toBe(200);
    expect(rangeOf(tp, 'height', all, { ...hidden, travel: true }).rows().map((r) => r.value)).toEqual([0.2]);
    const color = colorFunction(tp, 'feature', null, ['#FF0000']);
    const travel = [...tp.type].indexOf(MOVE.travel, 2);
    expect(color(travel)).toEqual([56, 72, 155]);
  });
});

describe('LegendPanel', () => {
  const tp = parseGcode(GCODE);
  const base = {
    tp,
    range: null,
    roles: Array(20).fill(true),
    options: { travel: false, retract: false, unretract: false, wipe: false, seam: true },
    colours: ['#FF0000', '#00FF00'],
    folded: false,
    o: (s: string) => s,
    phaseTitle: (n: number) => `phase ${n}`,
    onView: vi.fn(),
    onRole: vi.fn(),
    onOption: vi.fn(),
    onFold: vi.fn(),
  };

  it('lists the line types present with time, share and usage; a click shows or hides one', () => {
    const onRole = vi.fn();
    render(<LegendPanel {...base} view="feature" onRole={onRole} />);
    const legend = screen.getByTestId('preview-legend');
    expect(within(legend).getByText('Outer wall')).toBeInTheDocument();
    expect(within(legend).getByText('Support')).toBeInTheDocument();
    expect(within(legend).queryByText('Bridge')).not.toBeInTheDocument();
    fireEvent.click(within(legend).getByText('Outer wall'));
    expect(onRole).toHaveBeenCalledWith(ROLE['Outer wall']);
    expect(within(legend).getByText('Total estimation')).toBeInTheDocument();
    expect(within(legend).getByText('Prepare time:')).toBeInTheDocument();
    // the options present: travel always, then retract, unretract, wipe, seams
    for (const label of ['Travel', 'Retract', 'Unretract', 'Wipe', 'Seams']) expect(within(legend).getByText(label)).toBeInTheDocument();
  });

  it('keeps the view types that need the firmware simulation greyed', () => {
    render(<LegendPanel {...base} view="feature" />);
    const option = (name: string) => screen.getByRole('option', { name }) as HTMLOptionElement;
    expect(option('Actual Speed').disabled).toBe(true);
    expect(option('Actual Flow').disabled).toBe(true);
    expect(option('Fan Speed').disabled).toBe(false);
  });

  it('shows the filament table with only the columns in use, the changes and the cost', () => {
    render(<LegendPanel {...base} view="filament" />);
    const legend = screen.getByTestId('preview-legend');
    expect(within(legend).getByText('Support')).toBeInTheDocument();
    expect(within(legend).getByText('Flushed')).toBeInTheDocument();
    expect(within(legend).queryByText('Tower')).not.toBeInTheDocument();
    expect(within(legend).getByText('Filament change times:').nextSibling?.textContent).toBe('1');
    expect(within(legend).getByText('Cost:').nextSibling?.textContent).toBe('0.03');
  });

  it('folds to the combo alone', () => {
    render(<LegendPanel {...base} view="feature" folded />);
    expect(screen.queryByText('Total estimation')).not.toBeInTheDocument();
  });
});

describe('sliders (IMSlider, GLCanvas3D::on_key)', () => {
  const reduce = sliderReducer(10);
  const start = initialSliders(10);

  it('moves the active thumb, never past the other, and keeps the layers on the plate', () => {
    let s = reduce(start, { type: 'step', delta: -5 });
    s = reduce(s, { type: 'step', delta: -5 });
    expect([s.lo, s.hi]).toEqual([0, 0]);
    s = reduce(reduce(start, { type: 'active', which: 'lo' }), { type: 'step', delta: 5 });
    expect([s.lo, s.hi]).toEqual([5, 9]);
    s = reduce(s, { type: 'step', delta: 50 });
    expect([s.lo, s.hi]).toEqual([9, 9]);
    expect(reduce(start, { type: 'jump', layer: 3 }).hi).toBe(3);
  });

  it('one layer mode shows the active layer alone, and back', () => {
    let s = reduce(reduce(start, { type: 'step', delta: -2 }), { type: 'oneLayer' });
    expect([s.lo, s.hi, s.oneLayer]).toEqual([7, 7, true]);
    s = reduce(s, { type: 'step', delta: -3 });
    expect([s.lo, s.hi]).toEqual([4, 4]);
    s = reduce(s, { type: 'oneLayer' });
    expect([s.lo, s.hi, s.oneLayer]).toEqual([0, 4, false]);
  });

  it('steps the move slider, and past its ends into the layer before or after', () => {
    const moves = () => 3;
    let s = reduce(reduce(start, { type: 'step', delta: -4 }), { type: 'move', delta: -1, moves });
    expect([s.hi, s.move]).toEqual([5, 1]);
    s = reduce(reduce(s, { type: 'moveTo', index: 0 }), { type: 'move', delta: -1, moves });
    expect([s.hi, s.move]).toEqual([4, null]); // the layer before, at its last move
    s = reduce(s, { type: 'move', delta: 1, moves });
    expect([s.hi, s.move]).toEqual([5, 0]); // the layer after, at its first move
  });
});
