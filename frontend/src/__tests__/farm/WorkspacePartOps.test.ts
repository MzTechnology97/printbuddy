/**
 * Printfarm (fork): 3MF Workspace, batch E6b. A part's Delete, Change Type and Precise Seam Type as OrcaSlicer's part
 * menu has them: the objects as the operations leave them (parts sorted as sort_volumes, one part left taking the
 * object's place), Orca's refusals, the menu's entries, the plate's middle for Center, the undo step.
 */
import { describe, it, expect } from 'vitest';
import type { WorkspaceObject } from '../../farm/workspace/api';
import { draftHistory, type DraftHistory } from '../../farm/workspace/draft';
import { partMenu } from '../../farm/workspace/menus';
import { type PartOp, changeTypeTarget, deletedParts, plateCenter, refusal, withPartOps } from '../../farm/workspace/parts';

const BRACKET: WorkspaceObject = {
  id: '5',
  name: 'Bracket',
  extruder: '1',
  parts: [
    { id: '1', name: 'Body', type: 'normal_part' },
    { id: '3', name: 'Peg', type: 'normal_part', extruder: '2' },
    { id: '2', name: 'Box', type: 'modifier_part' },
  ],
  overrides: [
    { key: 'wall_loops', value: '4', part: null, part_id: null },
    { key: 'wall_loops', value: '5', part: 'Peg', part_id: '3' },
    { key: 'sparse_infill_density', value: '80%', part: 'Box', part_id: '2' },
  ],
};
const op = (o: Partial<PartOp>): PartOp => ({ op: 'type', object_id: '5', part_id: '1', ...o });
const ids = (o: WorkspaceObject) => o.parts.map((p) => `${p.id}:${p.type}`);

describe('the parts as the operations leave them (parts.ts)', () => {
  it('sorts the parts as sort_volumes does after a type change', () => {
    const [obj] = withPartOps([BRACKET], [op({ type: 'negative_part' })]);
    expect(ids(obj)).toEqual(['3:normal_part', '1:negative_part', '2:modifier_part']);
    // a seam goes last of its group, strong seams before weak ones; its settings are dormant
    const [seams] = withPartOps([BRACKET], [op({ part_id: '2', type: 'precise_seam_blocked' }), op({ part_id: '1', type: 'precise_seam_left' })]);
    expect(ids(seams)).toEqual(['3:normal_part', '1:precise_seam_left', '2:precise_seam_blocked']);
    expect(seams.overrides.some((o) => o.part_id === '2')).toBe(false);
  });

  it("gives the last part the object's place: its settings and filament the object's", () => {
    const [obj] = withPartOps([BRACKET], [op({ op: 'delete', part_id: '2' }), op({ op: 'delete', part_id: '1' })]);
    expect(ids(obj)).toEqual(['3:normal_part']);
    expect(obj.extruder).toBe('2');
    expect(obj.overrides).toEqual([{ key: 'wall_loops', value: '5', part: null, part_id: null }]);
    expect([...(deletedParts([op({ op: 'delete', part_id: '2' })]).get('5') ?? [])]).toEqual(['2']);
  });

  it("refuses as Orca does, with Orca's words", () => {
    const one = withPartOps([BRACKET], [op({ op: 'delete', part_id: '3' })])[0];
    expect(refusal(one, op({ op: 'delete' }))).toBe('Deleting the last solid part is not allowed.');
    expect(refusal(one, op({ type: 'modifier_part' }))).toBe('The type of the last solid object part cannot be changed.');
    expect(refusal(one, op({ op: 'delete', part_id: '2' }))).toBeNull();
    expect(refusal(BRACKET, op({ op: 'delete' }))).toBeNull();
  });

  it("keeps a seam's subtype on Change Type's Precise Seam", () => {
    expect(changeTypeTarget('precise_seam_left', 'precise_seam_center')).toBe('precise_seam_left');
    expect(changeTypeTarget('modifier_part', 'precise_seam_center')).toBe('precise_seam_center');
    expect(changeTypeTarget('precise_seam_left', 'negative_part')).toBe('negative_part');
  });

  it("finds the plate's middle as PartPlate::get_center_origin", () => {
    const bed = ['0x0', '256x0', '256x256', '0x256'];
    expect(plateCenter(0, 1, bed)).toEqual([128, 128]);
    // 3 plates in 2 columns: the third starts the second row, a bed and a fifth to the front
    expect(plateCenter(2, 3, bed)).toEqual([128, 128 - 307.2]);
    expect(plateCenter(1, 3, bed)?.[0]).toBeCloseTo(435.2);
  });
});

describe("the part menu's operations", () => {
  const on = { select: () => {}, zoom: () => {}, settings: () => {} };
  const tr = (s: string) => s;

  it('offers Change Type, and Precise Seam Type only for a seam', () => {
    const picked: [string, boolean][] = [];
    const set = (t: string, changeType: boolean) => picked.push([t, changeType]);
    const menu = partMenu(tr, 'Del', { ...on, remove: () => {}, type: { current: 'modifier_part', set, seams: true } });
    const change = menu.find((i) => i.label === 'Change Type')!;
    expect(change.items!.map((i) => `${i.label}${i.checked ? '*' : ''}`)).toEqual([
      'Part',
      'Negative Part',
      'Modifier*',
      'Support Blocker',
      'Support Enforcer',
      'Precise Seam',
    ]);
    expect(menu.some((i) => i.label === 'Precise Seam Type')).toBe(false);
    expect(menu.find((i) => i.label === 'Delete')?.onClick).toBeDefined();
    const seam = partMenu(tr, 'Del', { ...on, type: { current: 'precise_seam_right', set, seams: true } });
    const seamChange = seam.find((i) => i.label === 'Change Type')!;
    expect(seamChange.items!.find((i) => i.checked)?.label).toBe('Precise Seam');
    const subtype = seam.find((i) => i.label === 'Precise Seam Type')!;
    expect(subtype.items!.find((i) => i.checked)?.label).toBe('Seam Right');
    subtype.items![0].onClick!();
    seamChange.items![5].onClick!();
    expect(picked).toEqual([
      ['precise_seam_center', false],
      ['precise_seam_center', true],
    ]);
    // Bambu Studio has no Precise Seam
    const bambu = partMenu(tr, 'Del', { ...on, type: { current: 'normal_part', set, seams: false } });
    expect(bambu.find((i) => i.label === 'Change Type')!.items!.map((i) => i.label)).not.toContain('Precise Seam');
  });

  it("greys what it can't do", () => {
    const menu = partMenu(tr, 'Del', { ...on, type: { current: 'normal_part', seams: true } });
    expect(menu.filter((i) => i.onClick).map((i) => i.label)).toEqual(['Select', 'Zoom', 'Edit Process Settings']);
    expect(menu.find((i) => i.label === 'Change Type')?.submenu).toBe(true);
  });
});

describe('the undo history', () => {
  it('makes each part operation one step', () => {
    let h: DraftHistory = { draft: {}, past: [], future: [], last: null };
    h = draftHistory(h, { type: 'parts', partOps: [op({ op: 'delete', part_id: '2' })] });
    h = draftHistory(h, { type: 'parts', partOps: [op({ op: 'delete', part_id: '2' }), op({ type: 'negative_part' })] });
    expect(h.partOps).toHaveLength(2);
    h = draftHistory(h, { type: 'undo' });
    expect(h.partOps).toEqual([op({ op: 'delete', part_id: '2' })]);
    h = draftHistory(h, { type: 'undo' });
    expect(h.partOps ?? []).toEqual([]);
  });
});
