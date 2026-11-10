// Fork (printfarm): OrcaSlicer's Save preset and Compare presets dialogs (SavePresetDialog.cpp, DiffPresetDialog in
// UnsavedChangesDialog.cpp; v2.5.0). Save writes a user preset (Printbuddy's local presets): the preset it inherits
// and what differs from it, or, detached, every value. Compare shows two presets side by side, the project's own
// (its unsaved changes included), user presets or system presets, and can carry values from the left one into
// the project. "Preset Inside Project" waits on switching presets (it renames the project's preset).
import { useMemo, useState } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ArrowRight, Loader2, Save } from 'lucide-react';
import { api } from '../../api/client';
import { Button } from '../../components/Button';
import { type PresetValues, type SettingValue, type WorkspaceSetting, workspaceApi } from './api';
import { Dialog } from './Dialogs';
import type { Draft } from './draft';
import { type OrcaLayout, type Section, orcaFormat, orcaText } from './orcaLayout';
import { SECTION_TITLES, checkPresetName, presetDiff, projectPreset, sectionValues, suggestedName, userPresetSetting } from './presetTools';
import '../i18n';

function useOrca(layout: OrcaLayout) {
  const { i18n } = useTranslation();
  return {
    o: (text: string) => orcaText(layout, text, i18n.language),
    f: (text: string, ...values: string[]) => orcaFormat(layout, text, i18n.language, ...values),
  };
}

/** OrcaSlicer's UnsavedChangesDialog on a preset switch: the changes the switch would leave behind, kept over the
 * new preset (Transfer) or dropped (Discard). */
export function UnsavedChangesDialog({
  layout,
  preset,
  keys,
  onTransfer,
  onDiscard,
  onClose,
}: {
  layout: OrcaLayout;
  preset: string;
  keys: string[];
  onTransfer: () => void;
  onDiscard: () => void;
  onClose: () => void;
}) {
  const { o, f } = useOrca(layout);
  return (
    <Dialog title={o('Transfer or discard changes')} intro={f('Preset "%1%" contains the following unsaved changes:', preset)} onClose={onClose}>
      <ul className="p-4 space-y-1 overflow-y-auto text-sm text-white">
        {keys.map((k) => {
          // under its group, as the dialog's tree (a bare "Type" or "Enable" says little)
          const group = Object.values(layout.pages)
            .flat()
            .flatMap((page) => page.groups)
            .find((g) => g.keys.includes(k))?.title;
          return (
            <li key={k}>
              {group && <span className="text-bambu-gray">{o(group)}: </span>}
              {o(layout.options[k]?.label ?? k)}
            </li>
          );
        })}
      </ul>
      <div className="p-4 border-t border-bambu-dark-tertiary flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>
          {o('Cancel')}
        </Button>
        <Button variant="secondary" onClick={onDiscard}>
          {o('Discard')}
        </Button>
        <Button onClick={onTransfer} title={o('Transfer the selected options to the newly selected preset.')}>
          {o('Transfer')}
        </Button>
      </div>
    </Dialog>
  );
}

interface SaveProps {
  layout: OrcaLayout;
  section: Section;
  filament?: number;
  settings: WorkspaceSetting[];
  draft: Draft;
  onClose: () => void;
  onSaved: (name: string) => void;
}

export function SavePresetDialog({ layout, section, filament = 0, settings, draft, onClose, onSaved }: SaveProps) {
  const { t } = useTranslation();
  const { o, f } = useOrca(layout);
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: ['workspacePresets', section], queryFn: () => workspaceApi.presets(section) });
  const users = list.data?.user ?? [];
  const current = projectPreset(settings, section, filament);
  const currentUser = users.find((u) => u.name === current);
  const parent = currentUser ? currentUser.inherits : current;
  const [name, setName] = useState(() => suggestedName(current, !currentUser, o('Copy')));
  const [detach, setDetach] = useState(false);
  const check = checkPresetName(name, users.map((u) => u.name), currentUser || !current ? [] : [current]);
  const values = useMemo(() => sectionValues(settings, draft, section, filament), [settings, draft, section, filament]);
  const changed = useMemo(
    () => settings.filter((s) => s.section === section && (s.differs_from_system || s.key in draft)).map((s) => s.key),
    [settings, draft, section],
  );
  const save = useMutation({
    mutationFn: async () => {
      const setting = userPresetSetting(section, name, parent, values, changed, detach);
      const existing = users.find((u) => u.name === name);
      if (existing) await api.updateLocalPreset(existing.id, { setting });
      else await api.createLocalPreset({ name, preset_type: section, setting });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspacePresets', section] });
      onSaved(name);
    },
  });
  return (
    <Dialog title={o('Save preset')} onClose={onClose}>
      <form
        className="p-4 space-y-3 text-sm"
        onSubmit={(e) => {
          e.preventDefault();
          if (check.status !== 'invalid' && !save.isPending) save.mutate();
        }}
      >
        <label className="block">
          <span className="block mb-1">{f('Save %s as', o(SECTION_TITLES[section]))}:</span>
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={check.status === 'invalid'}
            className="w-full rounded bg-bambu-dark border border-bambu-dark-tertiary px-2 py-1 text-white"
          />
        </label>
        {check.messages.length > 0 && (
          <p className={`text-xs whitespace-pre-line ${check.status === 'invalid' ? 'text-red-400' : 'text-amber-400'}`} role="status">
            {check.messages.map((m) => f(m.text, ...(m.values ?? []))).join('\n')}
          </p>
        )}
        <fieldset className="space-y-1">
          <label className="flex items-center gap-2">
            <input type="radio" name="where" defaultChecked />
            {o('User Preset')}
          </label>
          <label className="flex items-center gap-2 text-bambu-gray" title={t('farm.workspace.presets.insideProjectLater')}>
            <input type="radio" name="where" disabled />
            {o('Preset Inside Project')}
          </label>
        </fieldset>
        <div className="space-y-1">
          <p className="text-xs text-bambu-gray">
            {o('Parent preset')}: {parent ?? o('Unique preset')}
          </p>
          <label
            className="flex items-center gap-2"
            title={o('Copies all inherited values from the parent into this preset and removes the parent relationship. Presets compatible only with the parent may become unsupported.')}
          >
            <input type="checkbox" checked={detach} onChange={(e) => setDetach(e.target.checked)} />
            {o(parent ? 'Detach from parent' : 'Save without parent')}
          </label>
        </div>
        {save.isError && <p className="text-xs text-red-400">{t('farm.workspace.presets.saveFailed')}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            {o('Cancel')}
          </Button>
          <Button type="submit" disabled={check.status === 'invalid' || save.isPending || list.isLoading}>
            {save.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {o('Save')}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** A side of the comparison: the project's preset (with its unsaved changes), a user preset or a system one. */
type Side = 'project' | `user:${number}` | `system:${string}`;
const SECTIONS: Section[] = ['printer', 'process', 'filament'];

interface CompareProps {
  layout: OrcaLayout;
  settings: WorkspaceSetting[];
  draft: Draft;
  editable: boolean;
  /** Carry a value into the project (its unsaved changes). */
  onTransfer: (setting: WorkspaceSetting, value: SettingValue) => void;
  onClose: () => void;
}

export function ComparePresetsDialog({ layout, settings, draft, editable, onTransfer, onClose }: CompareProps) {
  const { t } = useTranslation();
  const { o } = useOrca(layout);
  const [sides, setSides] = useState<Record<Section, [Side, Side]>>({
    printer: ['project', 'project'],
    process: ['project', 'project'],
    filament: ['project', 'project'],
  });
  const [filament, setFilament] = useState(0);
  const [transfer, setTransfer] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const lists = useQueries({
    queries: SECTIONS.map((s) => ({ queryKey: ['workspacePresets', s], queryFn: () => workspaceApi.presets(s) })),
  });
  const refs = SECTIONS.flatMap((s) => sides[s].filter((side) => side !== 'project').map((side) => [s, side] as const));
  const loaded = useQueries({
    queries: refs.map(([s, side]) => ({
      queryKey: ['workspacePresetValues', s, side],
      queryFn: () =>
        side.startsWith('user:')
          ? workspaceApi.presetValues(s, 'user', Number(side.slice(5)))
          : workspaceApi.presetValues(s, 'system', side.slice(7)),
      retry: false,
      staleTime: 60_000,
    })),
  });
  const valuesOf = (s: Section, side: Side): { values: Record<string, SettingValue> | null; error: boolean; missing: string | null } => {
    if (side === 'project') return { values: sectionValues(settings, draft, s, filament), error: false, missing: null };
    const q = loaded[refs.findIndex(([rs, rside]) => rs === s && rside === side)];
    const data = q?.data as PresetValues | undefined;
    return { values: data?.values ?? null, error: !!q?.isError, missing: data?.missing_parent ?? null };
  };
  const filaments = Math.max(1, ((settings.find((s) => s.key === 'filament_settings_id')?.value as string[] | undefined) ?? []).length);
  const options = (s: Section, i: number) => {
    const project = projectPreset(settings, s, s === 'filament' ? filament : 0);
    const users = lists[i].data?.user ?? [];
    // system presets: the ones the project and the user presets inherit from
    const system = [...new Set([project, ...users.map((u) => u.inherits)].filter((n): n is string => !!n && !users.some((u) => u.name === n)))];
    return [
      { value: 'project', label: `${project ?? '—'} (${t('farm.workspace.presets.project')})` },
      ...users.map((u) => ({ value: `user:${u.id}`, label: u.name })),
      ...system.map((n) => ({ value: `system:${n}`, label: n })),
    ];
  };
  const bySection = SECTIONS.map((s) => {
    const [l, r] = sides[s];
    const left = valuesOf(s, l), right = valuesOf(s, r);
    const keys = settings.filter((x) => x.section === s && x.readonly_reason !== 'identity').map((x) => x.key);
    const diff = left.values && right.values && l !== r ? new Set(presetDiff(left.values, right.values, keys)) : new Set<string>();
    return { s, left, right, diff, loading: (l !== 'project' && !left.values && !left.error) || (r !== 'project' && !right.values && !right.error) };
  });
  const canTransfer = (s: Section) => transfer && editable && sides[s][1] === 'project' && sides[s][0] !== 'project';
  const show = (v: SettingValue | undefined) => (v === undefined ? '' : Array.isArray(v) ? v.join(', ') : v);
  const doTransfer = () => {
    for (const { s, left } of bySection) {
      if (!canTransfer(s) || !left.values) continue;
      for (const key of picked) {
        const [ks, k] = key.split('|');
        if (ks !== s) continue;
        const setting = settings.find((x) => x.key === k);
        if (!setting || !setting.editable || left.values[k] === undefined) continue;
        let value = left.values[k];
        if (s === 'filament') {
          // the preset's value goes into that filament's element of the project's list
          const now = k in draft ? draft[k] : setting.value;
          const list = Array.isArray(now) ? [...now] : [String(now ?? '')];
          list[filament] = Array.isArray(value) ? value[0] : value;
          value = list;
        }
        onTransfer(setting, value);
      }
    }
    setPicked(new Set());
  };
  const any = bySection.some((b) => b.diff.size > 0);

  return (
    <Dialog title={o('Compare presets')} wide onClose={onClose}>
      <div className="p-4 space-y-3 text-sm overflow-y-auto">
        <table className="w-full">
          <tbody>
            {SECTIONS.map((s, i) => (
              <tr key={s}>
                <th className="pr-2 py-1 text-left font-normal text-bambu-gray-light whitespace-nowrap">{o(SECTION_TITLES[s])}</th>
                {[0, 1].map((side) => (
                  <td key={side} className="py-1 pr-2">
                    <div className="flex items-center gap-1">
                      {side === 1 && <ArrowRight className="w-3.5 h-3.5 text-bambu-gray flex-shrink-0" aria-hidden />}
                      <select
                        aria-label={`${o(SECTION_TITLES[s])} ${o(side ? 'Right Preset Value' : 'Left Preset Value')}`}
                        value={sides[s][side]}
                        onChange={(e) => setSides((cur) => ({ ...cur, [s]: (side ? [cur[s][0], e.target.value] : [e.target.value, cur[s][1]]) as [Side, Side] }))}
                        className="w-full min-w-0 rounded bg-bambu-dark border border-bambu-dark-tertiary px-1.5 py-1 text-white text-xs"
                      >
                        {options(s, i).map((op) => (
                          <option key={op.value} value={op.value}>
                            {op.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {filaments > 1 && (
          <label className="flex items-center gap-2 text-xs">
            {o('Filament')}
            <select value={filament} onChange={(e) => setFilament(Number(e.target.value))} className="rounded bg-bambu-dark border border-bambu-dark-tertiary px-1 py-0.5">
              {[...Array(filaments).keys()].map((i) => (
                <option key={i} value={i}>
                  {i + 1}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex items-center gap-2" title={o('If enabled, this dialog can be used for transfer selected values from left to right preset.')}>
          <input type="checkbox" checked={transfer} onChange={(e) => setTransfer(e.target.checked)} disabled={!editable} />
          {o('Transfer values from left to right')}
        </label>
        {transfer && SECTIONS.some((s) => sides[s][1] !== 'project' && sides[s][0] !== sides[s][1]) && (
          <p className="text-xs text-amber-400">{o('You can only transfer to current active profile because it has been modified.')}</p>
        )}
        {!any && !bySection.some((b) => b.loading) && <p className="text-bambu-gray">{o('Select presets to compare')}</p>}
        {bySection.map(({ s, left, right, diff, loading }) => {
          if (loading) return <p key={s} className="text-bambu-gray flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" />{o(SECTION_TITLES[s])}</p>;
          if (left.error || right.error) return <p key={s} className="text-red-400 text-xs">{o(SECTION_TITLES[s])}: {t('farm.workspace.presets.notFound')}</p>;
          const missing = left.missing ?? right.missing;
          if (!diff.size && !missing) return null;
          return (
            <section key={s}>
              <h3 className="font-semibold mt-2">{o(SECTION_TITLES[s])}</h3>
              {missing && <p className="text-xs text-amber-400">{t('farm.workspace.presets.missingParent', { name: missing })}</p>}
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-bambu-gray border-b border-bambu-dark-tertiary">
                    {canTransfer(s) && <th className="w-6" />}
                    <th className="py-1" />
                    <th className="py-1">{o('Left Preset Value')}</th>
                    <th className="py-1">{o('Right Preset Value')}</th>
                  </tr>
                </thead>
                <tbody>
                  {layout.pages[s].flatMap((page) =>
                    page.groups.flatMap((group) =>
                      group.keys
                        .filter((k) => diff.has(k))
                        .map((k) => (
                          <tr key={`${page.title}|${group.title}|${k}`} className="border-b border-bambu-dark-tertiary/50 align-top">
                            {canTransfer(s) && (
                              <td>
                                <input
                                  type="checkbox"
                                  aria-label={o(layout.options[k]?.label ?? k)}
                                  checked={picked.has(`${s}|${k}`)}
                                  onChange={(e) =>
                                    setPicked((p) => {
                                      const n = new Set(p);
                                      if (e.target.checked) n.add(`${s}|${k}`);
                                      else n.delete(`${s}|${k}`);
                                      return n;
                                    })
                                  }
                                />
                              </td>
                            )}
                            <td className="py-1 pr-2">
                              <span className="text-bambu-gray">{o(page.title)} › </span>
                              {o(layout.options[k]?.label ?? k)}
                            </td>
                            <td className="py-1 pr-2 break-all" title={show(left.values?.[k])}>{show(left.values?.[k])}</td>
                            <td className="py-1 break-all" title={show(right.values?.[k])}>{show(right.values?.[k])}</td>
                          </tr>
                        )),
                    ),
                  )}
                </tbody>
              </table>
            </section>
          );
        })}
        <div className="flex justify-end gap-2 pt-2">
          {transfer && (
            <Button onClick={doTransfer} disabled={picked.size === 0}>
              {o('Transfer')}
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>
            {t('farm.workspace.close')}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
