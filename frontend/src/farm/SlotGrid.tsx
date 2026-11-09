// Printfarm (fork): the slot mapping as the slicers' send dialog shows it. Per filament of the plate, the
// printer's AMS/AMS HT/CFS units and external spool with every slot in its spool's colour; a click picks
// that slot (upstream's manual mapping). Slots on the other nozzle and empty ones can't be picked.
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Check } from 'lucide-react';
import { api } from '../api/client';
import type { AMSTray, PrinterStatus } from '../api/client';
import type { FilamentReqsData } from '../components/PrintModal/types';
import { useFilamentMapping } from '../hooks/useFilamentMapping';
import { getGlobalTrayId, normalizeColor } from '../utils/amsHelpers';
import { getColorName } from '../utils/colors';
import './i18n';

interface Slot {
  id: number; // global tray id, as ams_mapping wants it
  label: string;
  tray: AMSTray;
  extruder?: number; // 0 right, 1 left (dual nozzle)
}

interface Unit {
  key: string;
  name: string;
  slots: Slot[];
}

const letter = (amsId: number) => String.fromCharCode(65 + (amsId >= 128 ? amsId - 128 : amsId));

/** The printer's units, empty slots included, in the order the printer reports them. */
function slotUnits(status: PrinterStatus | undefined, external: string): Unit[] {
  const extruders = status?.ams_extruder_map ?? {};
  const units: Unit[] = (status?.ams ?? []).map((ams) => {
    const ht = ams.is_ams_ht || ams.tray.length === 1;
    const name = ams.module_type === 'cfs' ? `CFS T${ams.id + 1}` : ht ? `AMS HT-${letter(ams.id)}` : `AMS ${letter(ams.id)}`;
    return {
      key: `ams-${ams.id}`,
      name,
      slots: ams.tray.map((tray) => ({
        id: getGlobalTrayId(ams.id, tray.id, false),
        label: ht ? `HT-${letter(ams.id)}` : `${letter(ams.id)}${tray.id + 1}`,
        tray,
        extruder: extruders[String(ams.id)],
      })),
    };
  });
  const vt = status?.vt_tray ?? [];
  if (vt.some((tray) => tray.tray_type)) {
    const dual = vt.length > 1;
    units.push({
      key: 'ext',
      name: external,
      slots: vt.map((tray) => {
        const id = tray.id ?? 254;
        return { id, label: dual ? (id === 254 ? 'Ext-L' : 'Ext-R') : 'Ext', tray, extruder: dual ? 255 - id : undefined };
      }),
    });
  }
  return units;
}

const Dot = ({ color, size }: { color: string | null; size: number }) => (
  <span aria-hidden className="inline-block rounded-full border border-bambu-gray/60 shrink-0"
    style={{ width: size, height: size, background: color ?? 'transparent' }} />
);

export function SlotGrid({ printerId, filamentReqs, mappings, onMappings, preferLowest }: {
  printerId: number;
  preferLowest?: boolean; // as the dialog's own automatic proposal
  filamentReqs: FilamentReqsData | undefined;
  mappings: Record<number, number>;
  onMappings: (m: Record<number, number>) => void;
}) {
  const { t } = useTranslation();
  const status = useQuery({ queryKey: ['printer-status', printerId], queryFn: () => api.getPrinterStatus(printerId) });
  const assignments = useQuery({ queryKey: ['spool-assignments', printerId], queryFn: () => api.getAssignments(printerId) });
  const { filamentComparison } = useFilamentMapping(filamentReqs, status.data, mappings, preferLowest,
    assignments.data, printerId);
  const units = useMemo(() => slotUnits(status.data, t('farm.jobs.slotExternal')), [status.data, t]);
  const gramsLeft = useMemo(() => {
    const map = new Map<number, number>();
    for (const a of assignments.data ?? []) {
      if (a.spool?.label_weight) {
        map.set(getGlobalTrayId(a.ams_id, a.tray_id, a.ams_id === 255),
          Math.max(0, Math.round(a.spool.label_weight - (a.spool.weight_used ?? 0))));
      }
    }
    return map;
  }, [assignments.data]);
  const fts = status.data?.fila_switch?.installed === true; // any slot reaches either nozzle
  if (!status.data || !filamentComparison.length) return null;

  const left = (slot: Slot) => {
    const grams = gramsLeft.get(slot.id);
    if (grams != null) return t('farm.jobs.slotGramsLeft', { grams });
    return slot.tray.remain >= 0 ? `${slot.tray.remain}%` : '';
  };
  const describe = (slot: Slot) =>
    [slot.tray.tray_sub_brands || slot.tray.tray_type, getColorName(normalizeColor(slot.tray.tray_color))]
      .filter(Boolean).join(' ');

  return (
    <div className="space-y-3">
      {filamentComparison.map((f) => {
        const reach = (slot: Slot) => fts || f.nozzle_id == null || slot.extruder === f.nozzle_id;
        const chosen = units.flatMap((u) => u.slots).find((s) => s.id === f.loaded?.globalTrayId);
        const ok = f.status === 'match';
        return (
          <div key={f.slot_id} className="bg-bambu-dark rounded-lg p-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Dot color={f.color} size={14} />
              <span className="text-white font-medium">{f.slot_id} · {f.type}</span>
              {f.nozzle_id != null && (
                <span className="text-xs border border-bambu-gray/50 rounded px-1.5 text-bambu-gray">
                  {t(f.nozzle_id === 1 ? 'farm.jobs.slotNozzleLeft' : 'farm.jobs.slotNozzleRight')}
                </span>
              )}
              {f.used_grams > 0 && <span className="text-xs text-bambu-gray">{f.used_grams} g</span>}
              <span className="ml-auto flex items-center gap-2 min-w-0">
                {chosen ? (
                  <>
                    <Dot color={normalizeColor(chosen.tray.tray_color)} size={16} />
                    <span className="text-white truncate">
                      <span className="font-medium">{chosen.label}</span> · {describe(chosen)}
                      {left(chosen) && <span className="text-bambu-gray"> · {left(chosen)}</span>}
                    </span>
                    {ok
                      ? <Check className="w-4 h-4 text-bambu-green shrink-0" aria-label={t('farm.jobs.slotMatch')} />
                      : <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" aria-hidden />}
                  </>
                ) : <span className="text-amber-400">{t('farm.jobs.slotNone')}</span>}
                {mappings[f.slot_id] != null && (
                  <button type="button" className="text-xs text-bambu-gray underline"
                    onClick={() => { const next = { ...mappings }; delete next[f.slot_id]; onMappings(next); }}>
                    {t('farm.jobs.slotAuto')}
                  </button>
                )}
              </span>
            </div>
            {chosen && !ok && (
              <p className="text-xs text-amber-400">
                {f.typeMatch
                  ? t('farm.jobs.slotColourDiffers')
                  : t('farm.jobs.slotTypeDiffers', { loaded: chosen.tray.tray_type, wanted: f.type })}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              {units.map((unit) => {
                const away = unit.slots.every((s) => !reach(s));
                return (
                  <div key={unit.key} className={`bg-bambu-dark-secondary rounded-md px-2.5 py-2 ${away ? 'opacity-40' : ''}`}>
                    <div className="text-xs text-bambu-gray mb-1.5">{unit.name}</div>
                    <div className="flex gap-2">
                      {unit.slots.map((slot) => {
                        const empty = !slot.tray.tray_type;
                        const picked = slot.id === f.loaded?.globalTrayId;
                        return (
                          <button key={slot.id} type="button" disabled={empty || !reach(slot)} aria-pressed={picked}
                            title={empty ? t('farm.jobs.slotEmpty') : `${slot.label} · ${describe(slot)}`}
                            onClick={() => onMappings({ ...mappings, [f.slot_id]: slot.id })}
                            className="flex flex-col items-center gap-0.5 disabled:cursor-not-allowed">
                            <span
                              className={`block w-8 h-8 rounded-full border border-bambu-gray/60 ${empty ? 'border-dashed' : ''} ${
                                picked ? 'ring-2 ring-offset-2 ring-offset-bambu-dark-secondary ring-bambu-green' : ''}`}
                              style={{ background: empty ? 'transparent' : normalizeColor(slot.tray.tray_color) }} />
                            <span className={`text-[11px] ${picked ? 'text-white font-medium' : 'text-bambu-gray'}`}>{slot.label}</span>
                            <span className="text-[10px] text-bambu-gray">{empty ? t('farm.jobs.slotEmpty') : slot.tray.tray_type}</span>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
