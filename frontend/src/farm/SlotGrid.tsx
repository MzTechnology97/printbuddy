// Printfarm (fork): the slot mapping as the slicers' send dialog shows it. Per filament of the plate, the
// printer's AMS/AMS HT/CFS units and external spool with every slot in its spool's colour; a click picks
// that slot (upstream's manual mapping). Slots on the other nozzle and empty ones can't be picked.
// The loaded slots, their ids and nozzles are upstream's (buildLoadedFilaments), so the ring is the slot sent.
import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Check, RefreshCw } from 'lucide-react';
import { api } from '../api/client';
import type { PrinterStatus } from '../api/client';
import type { FilamentReqsData } from '../components/PrintModal/types';
import { useFilamentMapping } from '../hooks/useFilamentMapping';
import type { LoadedFilament } from '../hooks/useFilamentMapping';
import { getGlobalTrayId } from '../utils/amsHelpers';
import './i18n';

interface Slot {
  id: number; // global tray id, as ams_mapping wants it
  label: string;
  loaded?: LoadedFilament; // none: empty
  extruder?: number; // 0 right, 1 left (dual nozzle)
}

interface Unit {
  key: string;
  name: string;
  slots: Slot[];
}

const letter = (amsId: number) => String.fromCharCode(65 + (amsId >= 128 ? amsId - 128 : amsId));

/** The printer's units with their empty slots, the loaded ones as upstream sees them. */
function slotUnits(status: PrinterStatus | undefined, loaded: LoadedFilament[], external: string): Unit[] {
  const extruders = status?.ams_extruder_map ?? {};
  const byId = new Map(loaded.map((f) => [f.globalTrayId, f]));
  const units: Unit[] = (status?.ams ?? []).map((ams) => {
    const ht = ams.is_ams_ht || ams.tray.length === 1;
    return {
      key: `ams-${ams.id}`,
      name: ams.module_type === 'cfs' ? `CFS T${ams.id + 1}` : ht ? `AMS HT-${letter(ams.id)}` : `AMS ${letter(ams.id)}`,
      slots: ams.tray.map((tray) => {
        const id = getGlobalTrayId(ams.id, tray.id, false);
        const f = byId.get(id);
        return { id, label: ht ? `HT-${letter(ams.id)}` : `${letter(ams.id)}${tray.id + 1}`, loaded: f,
          extruder: f ? f.extruderId : extruders[String(ams.id)] };
      }),
    };
  });
  // External spools: those upstream maps (a spool set by hand included), plus the empty ones of a dual holder.
  const ext: Slot[] = loaded.filter((f) => f.isExternal)
    .map((f) => ({ id: f.globalTrayId, label: f.label === 'External' ? 'Ext' : f.label, loaded: f, extruder: f.extruderId }));
  const vt = status?.vt_tray ?? [];
  if (vt.length > 1) {
    for (const tray of vt) {
      const id = tray.id ?? 254;
      if (!ext.some((s) => s.id === id)) ext.push({ id, label: id === 254 ? 'Ext-L' : 'Ext-R', extruder: 255 - id });
    }
  }
  if (ext.some((s) => s.loaded)) units.push({ key: 'ext', name: external, slots: ext.sort((a, b) => a.id - b.id) });
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
  const queryClient = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const status = useQuery({ queryKey: ['printer-status', printerId], queryFn: () => api.getPrinterStatus(printerId) });
  const assignments = useQuery({ queryKey: ['spool-assignments', printerId], queryFn: () => api.getAssignments(printerId) });
  const { loadedFilaments, filamentComparison } = useFilamentMapping(filamentReqs, status.data, mappings, preferLowest,
    assignments.data, printerId);
  const units = useMemo(() => slotUnits(status.data, loadedFilaments, t('farm.jobs.slotExternal')),
    [status.data, loadedFilaments, t]);
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

  const refresh = async () => {
    setRefreshing(true);
    try {
      await api.refreshPrinterStatus(printerId);
      await new Promise((resolve) => setTimeout(resolve, 1000)); // the printer answers on MQTT
      await queryClient.invalidateQueries({ queryKey: ['printer-status', printerId] });
    } finally {
      setRefreshing(false);
    }
  };
  const left = (slot: Slot) => {
    const grams = gramsLeft.get(slot.id);
    if (grams != null) return t('farm.jobs.slotGramsLeft', { grams });
    return slot.loaded && slot.loaded.remain >= 0 ? `${slot.loaded.remain}%` : '';
  };
  const describe = (slot: Slot) =>
    slot.loaded ? [slot.loaded.traySubBrands || slot.loaded.type, slot.loaded.colorName].filter(Boolean).join(' ') : '';

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button type="button" onClick={refresh} disabled={refreshing}
          className="inline-flex items-center gap-1 text-xs text-bambu-gray hover:text-white disabled:opacity-50">
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} aria-hidden />
          {t('farm.jobs.slotRefresh')}
        </button>
      </div>
      {filamentComparison.map((f) => {
        const reach = (slot: Slot) => fts || f.nozzle_id == null || slot.extruder === f.nozzle_id;
        const chosen = units.flatMap((u) => u.slots).find((s) => s.loaded && s.id === f.loaded?.globalTrayId);
        const ok = f.status === 'match';
        return (
          <div key={f.slot_id} role="group" aria-label={t('farm.jobs.slotFilament', { n: f.slot_id, type: f.type })}
            className="bg-bambu-dark rounded-lg p-3 space-y-2">
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
                {chosen?.loaded ? (
                  <>
                    <Dot color={chosen.loaded.color} size={16} />
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
            {chosen?.loaded && !ok && (
              <p className="text-xs text-amber-400">
                {f.typeMatch
                  ? t('farm.jobs.slotColourDiffers')
                  : t('farm.jobs.slotTypeDiffers', { loaded: chosen.loaded.type, wanted: f.type })}
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
                        const picked = !!slot.loaded && slot.id === f.loaded?.globalTrayId;
                        const name = slot.loaded ? `${slot.label} · ${describe(slot)}` : `${slot.label} · ${t('farm.jobs.slotEmpty')}`;
                        return (
                          <button key={slot.id} type="button" disabled={!slot.loaded || !reach(slot)} aria-pressed={picked}
                            title={name} aria-label={t('farm.jobs.slotPick', { n: f.slot_id, slot: name })}
                            onClick={() => onMappings({ ...mappings, [f.slot_id]: slot.id })}
                            className="flex flex-col items-center gap-0.5 disabled:cursor-not-allowed">
                            <span
                              className={`block w-8 h-8 rounded-full border border-bambu-gray/60 ${slot.loaded ? '' : 'border-dashed'} ${
                                picked ? 'ring-2 ring-offset-2 ring-offset-bambu-dark-secondary ring-bambu-green' : ''}`}
                              style={{ background: slot.loaded ? slot.loaded.color : 'transparent' }} />
                            <span className={`text-[11px] ${picked ? 'text-white font-medium' : 'text-bambu-gray'}`}>{slot.label}</span>
                            <span className="text-[10px] text-bambu-gray">{slot.loaded ? slot.loaded.type : t('farm.jobs.slotEmpty')}</span>
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
