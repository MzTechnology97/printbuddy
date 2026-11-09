// Fork (printfarm): the Preview legend, as GCodeViewer::render_legend draws it (OrcaSlicer v2.5.0): the view type
// combo with its fold button, then per view type the line types with time, share and filament (each one shown
// or hidden with a click), a value scale, or the filament table; the options; and the time estimation.
import { ChevronDown, ChevronUp, Eye, EyeOff } from 'lucide-react';
import {
  type ColorRange,
  type OptionKey,
  type ViewType,
  MOTION_VIEWS,
  OPTIONS,
  ROLE_COLORS,
  VIEW_TYPES,
  compactCount,
  compactWeight,
  distance,
  filamentOf,
  percent,
  presentRoles,
  rgb,
  shortTime,
} from './legend';
import { ROLES, type Toolpaths } from './parse';

interface Props {
  tp: Toolpaths;
  view: ViewType;
  onView: (v: ViewType) => void;
  range: ColorRange | null;
  roles: boolean[];
  onRole: (role: number) => void;
  options: Record<OptionKey, boolean>;
  onOption: (key: OptionKey) => void;
  colours: string[];
  folded: boolean;
  onFold: () => void;
  /** An OrcaSlicer string in the UI language. */
  o: (text: string) => string;
  phaseTitle: (phase: number) => string;
}

const cell = 'px-1.5 py-0.5 text-right tabular-nums whitespace-nowrap';

function Swatch({ color }: { color: string }) {
  return <span className="inline-block w-3 h-3 rounded-sm border border-white/20 flex-shrink-0" style={{ backgroundColor: color }} aria-hidden />;
}

function EyeToggle({ on }: { on: boolean }) {
  return on ? <Eye className="w-3.5 h-3.5" aria-hidden /> : <EyeOff className="w-3.5 h-3.5 opacity-60" aria-hidden />;
}

export function LegendPanel({ tp, view, onView, range, roles, onRole, options, onOption, colours, folded, onFold, o, phaseTitle }: Props) {
  const s = tp.stats;
  const total = s.totalTime;
  const extruders = s.usedExtruders.length ? s.usedExtruders : [0];
  const slot0 = extruders[0];
  const fil = (mm3: number, slot = slot0) => filamentOf(tp, mm3, slot);
  const sum = (a: number[]) => a.reduce((x, y) => x + (y || 0), 0);
  const totalM = sum(s.usedMm) / 1000;
  const totalG = sum(s.usedG);
  const cost = sum(s.cost);
  const excluded = (key: 'supportMm3' | 'flushMm3' | 'towerMm3') =>
    extruders.reduce((acc, e) => {
      const f = fil(s[key][e] ?? 0, e);
      return { m: acc.m + f.m, g: acc.g + f.g };
    }, { m: 0, g: 0 });
  const presentOptions = OPTIONS.filter((op) => op.key === 'travel' || s.typeCount[op.move] > 0);

  const optionRow = (op: (typeof OPTIONS)[number], full: boolean) => {
    const time = s.typeTime[op.move];
    const dist = op.key === 'travel' || op.key === 'wipe' || op.key === 'retract' || op.key === 'unretract' || op.key === 'seam';
    return (
      <tr key={op.key} className={`cursor-pointer hover:bg-white/5 ${options[op.key] ? '' : 'text-bambu-gray'}`} onClick={() => onOption(op.key)}>
        <td className="px-1.5 py-0.5">
          <span className="flex items-center gap-1.5">
            <Swatch color={rgb(op.color)} />
            {o(op.label)}
          </span>
        </td>
        {full && (
          <>
            <td className={cell}>{time > 0 ? shortTime(time) : ''}</td>
            <td className={cell}>{total > 0 ? percent(time / total) : ''}</td>
            <td className={cell}>{dist ? distance(s.typeDistance[op.move]) : ''}</td>
            <td className={cell}>{compactCount(s.typeCount[op.move])}</td>
          </>
        )}
        <td className="px-1.5 py-0.5" aria-label={o(op.label)} aria-pressed={options[op.key]}>
          <EyeToggle on={options[op.key]} />
        </td>
      </tr>
    );
  };

  const stats = () => {
    const sup = excluded('supportMm3'), flu = excluded('flushMm3'), tow = excluded('towerMm3');
    const rows: [string, string][] = [];
    if (view === 'feature') {
      rows.push([o('Total Filament'), `${totalM.toFixed(2)} m  ${compactWeight(totalG)}`]);
      rows.push([o('Model Filament'), `${(totalM - sup.m - flu.m - tow.m).toFixed(2)} m  ${compactWeight(totalG - sup.g - flu.g - tow.g)}`]);
      rows.push([o('Cost'), cost.toFixed(2)]);
    }
    if (s.prepareTime) rows.push([o('Prepare time'), shortTime(s.prepareTime)]);
    rows.push([o('Model printing time'), shortTime(total - s.prepareTime)]);
    rows.push([o('Total time'), shortTime(total)]);
    return (
      <div className="mt-2 border-t border-white/10 pt-1.5">
        <p className="font-semibold px-1.5">{o(view === 'feature' ? 'Total estimation' : 'Time Estimation')}</p>
        <table className="w-full">
          <tbody>
            {rows.map(([k, v]) => (
              <tr key={k}>
                <td className="px-1.5 py-0.5 text-bambu-gray-light">{k}:</td>
                <td className="px-1.5 py-0.5 tabular-nums whitespace-pre">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  };

  const body = () => {
    if (view === 'summary') {
      return (
        <table className="w-full">
          <tbody>
            <tr><td className="px-1.5 py-0.5">{o('Total')}:</td><td className={cell}>{`${totalM.toFixed(2)} m / ${compactWeight(totalG)}`}</td></tr>
            <tr><td className="px-1.5 py-0.5">{o('Cost')}:</td><td className={cell}>{cost.toFixed(2)}</td></tr>
            <tr><td className="px-1.5 py-0.5">{o('Total time')}:</td><td className={cell}>{shortTime(total)}</td></tr>
          </tbody>
        </table>
      );
    }
    if (view === 'feature') {
      return (
        <>
          <table className="w-full">
            <thead>
              <tr className="font-semibold border-b border-white/10">
                <th className="px-1.5 py-0.5 text-left">{o('Line Type')}</th>
                <th className={cell}>{o('Time')}</th>
                <th className={cell}>%</th>
                <th className={cell} colSpan={2}>{o('Usage')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {presentRoles(tp).map((r) => {
                const f = fil(s.roleMm3[r]);
                const time = s.roleTime[r];
                return (
                  <tr key={r} className={`cursor-pointer hover:bg-white/5 ${roles[r] ? '' : 'text-bambu-gray'}`} onClick={() => onRole(r)}>
                    <td className="px-1.5 py-0.5">
                      <span className="flex items-center gap-1.5">
                        <Swatch color={rgb(ROLE_COLORS[r])} />
                        {o(ROLES[r])}
                      </span>
                    </td>
                    <td className={cell}>{time > 0 ? shortTime(time) : ''}</td>
                    <td className={cell}>{total > 0 ? percent(time / total) : '0'}</td>
                    <td className={cell}>{`${f.m.toFixed(2)}m`}</td>
                    <td className={cell}>{compactWeight(f.g)}</td>
                    <td className="px-1.5 py-0.5" aria-label={o(ROLES[r])} aria-pressed={roles[r]}>
                      <EyeToggle on={roles[r]} />
                    </td>
                  </tr>
                );
              })}
              {presentOptions.map((op) => optionRow(op, true))}
            </tbody>
          </table>
          {stats()}
        </>
      );
    }
    if (view === 'filament') {
      const cols = {
        support: extruders.some((e) => (s.supportMm3[e] ?? 0) > 0),
        flushed: extruders.some((e) => (s.flushMm3[e] ?? 0) > 0),
        tower: extruders.some((e) => (s.towerMm3[e] ?? 0) > 0),
      };
      const extra = cols.support || cols.flushed || cols.tower;
      const two = (f: { m: number; g: number }) => (
        <span className="whitespace-pre">{extra ? `${f.m.toFixed(2)} m\n${compactWeight(f.g)}` : `${f.m.toFixed(2)} m    ${compactWeight(f.g)}`}</span>
      );
      const per = extruders.map((e) => {
        const model = fil(s.modelMm3[e] ?? 0, e), support = fil(s.supportMm3[e] ?? 0, e);
        const flushed = fil(s.flushMm3[e] ?? 0, e), tower = fil(s.towerMm3[e] ?? 0, e);
        const tot = { m: model.m + support.m + flushed.m + tower.m, g: model.g + support.g + flushed.g + tower.g };
        return { e, model, support, flushed, tower, tot };
      });
      const add = (k: 'model' | 'support' | 'flushed' | 'tower' | 'tot') =>
        per.reduce((acc, p) => ({ m: acc.m + p[k].m, g: acc.g + p[k].g }), { m: 0, g: 0 });
      return (
        <>
          <table className="w-full">
            <thead>
              <tr className="font-semibold border-b border-white/10">
                <th className="px-1.5 py-0.5 text-left">{o('Filament')}</th>
                <th className={cell}>{o('Model')}</th>
                {cols.support && <th className={cell}>{o('Support')}</th>}
                {cols.flushed && <th className={cell}>{o('Flushed')}</th>}
                {cols.tower && <th className={cell}>{o('Tower')}</th>}
                {extra && <th className={cell}>{o('Total')}</th>}
              </tr>
            </thead>
            <tbody>
              {per.map((p) => (
                <tr key={p.e}>
                  <td className="px-1.5 py-0.5">
                    <span className="flex items-center gap-1.5">
                      <Swatch color={colours[p.e] ?? '#cccccc'} />
                      {p.e + 1}
                    </span>
                  </td>
                  <td className={cell}>{two(p.model)}</td>
                  {cols.support && <td className={cell}>{two(p.support)}</td>}
                  {cols.flushed && <td className={cell}>{two(p.flushed)}</td>}
                  {cols.tower && <td className={cell}>{two(p.tower)}</td>}
                  {extra && <td className={cell}>{two(p.tot)}</td>}
                </tr>
              ))}
              {per.length > 1 && (
                <tr className="border-t border-white/10">
                  <td className="px-1.5 py-0.5">{o('Total')}</td>
                  <td className={cell}>{two(add('model'))}</td>
                  {cols.support && <td className={cell}>{two(add('support'))}</td>}
                  {cols.flushed && <td className={cell}>{two(add('flushed'))}</td>}
                  {cols.tower && <td className={cell}>{two(add('tower'))}</td>}
                  {extra && <td className={cell}>{two(add('tot'))}</td>}
                </tr>
              )}
            </tbody>
          </table>
          <table className="mt-1">
            <tbody>
              <tr><td className="px-1.5 py-0.5">{o('Filament change times')}:</td><td className={cell}>{compactCount(s.filamentChanges)}</td></tr>
              <tr><td className="px-1.5 py-0.5">{o('Tool changes')}:</td><td className={cell}>{compactCount(s.extruderChanges)}</td></tr>
              <tr><td className="px-1.5 py-0.5">{o('Cost')}:</td><td className={cell}>{cost.toFixed(2)}</td></tr>
            </tbody>
          </table>
          {stats()}
          <table className="w-full mt-2">
            <thead>
              <tr className="font-semibold border-b border-white/10">
                <th className="px-1.5 py-0.5 text-left">{o('Options')}</th>
                <th />
              </tr>
            </thead>
            <tbody>{presentOptions.map((op) => optionRow(op, false))}</tbody>
          </table>
        </>
      );
    }
    const meta = VIEW_TYPES.find((v) => v.key === view) as { title: string; decimals: number };
    const motion = MOTION_VIEWS.includes(view);
    return (
      <>
        <p className="font-semibold px-1.5 pb-0.5 border-b border-white/10">{o(meta.title)}</p>
        <ul className="py-0.5">
          {(range?.rows() ?? []).map((row, k) => (
            <li key={k} className="flex items-center gap-1.5 px-1.5 py-0.5 tabular-nums">
              <Swatch color={rgb(row.color)} />
              {view === 'layerTime' || view === 'layerTimeLog' ? shortTime(row.value) : row.value.toFixed(meta.decimals)}
            </li>
          ))}
        </ul>
        {motion && (
          <table className="w-full">
            <thead>
              <tr className="font-semibold border-b border-white/10">
                <th className="px-1.5 py-0.5 text-left">{o('Options')}</th>
                <th />
              </tr>
            </thead>
            <tbody>{optionRow(OPTIONS[0], false)}</tbody>
          </table>
        )}
        {stats()}
      </>
    );
  };

  return (
    <div className="absolute top-2 left-2 z-10 max-w-[calc(100%-5rem)] max-h-[calc(100%-6rem)] overflow-auto rounded-md bg-bambu-dark-secondary/90 p-1.5 text-xs text-white shadow-lg" data-testid="preview-legend">
      <div className="flex items-center gap-1">
        <button type="button" onClick={onFold} className="p-0.5 rounded hover:bg-white/10" aria-expanded={!folded} aria-label="legend">
          {folded ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
        </button>
        <select
          value={view}
          onChange={(e) => onView(e.target.value as ViewType)}
          className="flex-1 min-w-0 rounded bg-bambu-dark-tertiary px-1.5 py-0.5 text-xs text-white border border-white/10"
          aria-label={o('Line Type')}
        >
          {VIEW_TYPES.map((v) => (
            <option key={v.key} value={v.key} disabled={'phase' in v} title={'phase' in v ? phaseTitle(v.phase) : undefined}>
              {o(v.label)}
            </option>
          ))}
        </select>
      </div>
      {!folded && <div className="mt-1.5">{body()}</div>}
    </div>
  );
}
