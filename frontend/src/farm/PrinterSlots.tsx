// Printfarm (fork): what a chosen printer has loaded, and the plate's AMS/CFS slots, with upstream's mapping.
import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import { FilamentMapping } from '../components/PrintModal/FilamentMapping';
import type { FilamentReqsData } from '../components/PrintModal/types';
import { buildLoadedFilaments, useFilamentMapping } from '../hooks/useFilamentMapping';
import { getCurrencySymbol } from '../utils/currency';
import { farmApi } from './api';
import './i18n';

const Swatch = ({ color }: { color: string }) => (
  <span aria-hidden className="w-3 h-3 rounded-full border border-bambu-gray/60 shrink-0" style={{ background: color }} />
);

/** Slots as the printer reports them (AMS, CFS, external spool); a Klipper without CFS: the spool set by hand. */
function Loaded({ printerId }: { printerId: number }) {
  const { t } = useTranslation();
  const status = useQuery({ queryKey: ['printer-status', printerId], queryFn: () => api.getPrinterStatus(printerId) });
  const spools = useQuery({ queryKey: ['farm', 'printer-spools'], queryFn: farmApi.getPrinterSpools });
  const loaded = buildLoadedFilaments(status.data);
  const byHand = spools.data?.find((s) => s.printer_id === printerId && s.material);
  if (status.isLoading) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <span className="text-bambu-gray">{t('farm.jobs.loaded')}</span>
      {loaded.map((f) => (
        <span key={f.globalTrayId} className="inline-flex items-center gap-1 bg-bambu-dark rounded px-2 py-0.5 text-white">
          <Swatch color={f.color} />
          {f.label}: {f.traySubBrands || f.type} ({f.colorName})
        </span>
      ))}
      {!loaded.length && byHand && (
        <span className="inline-flex items-center gap-1 bg-bambu-dark rounded px-2 py-0.5 text-white">
          {byHand.color && <Swatch color={`#${byHand.color}`} />}
          {byHand.material} {byHand.color_name} · {t('farm.spools.byHand')}
        </span>
      )}
      {!loaded.length && !byHand && <span className="text-amber-400">{t('farm.jobs.nothingLoaded')}</span>}
    </div>
  );
}

/** Upstream's slot mapping for this plate on this printer; reports the AMS mapping when slots were chosen by hand. */
export function PrinterSlots({ printerId, plateId, plateLabel, color, colorHex, mappings, onMappings, onAmsMapping }: {
  printerId: number;
  plateId: number;
  color: string | null; // the component's colour: its slice of the plate
  colorHex: string | null; // which the automatic proposal looks for
  plateLabel: string;
  mappings: Record<number, number>;
  onMappings: (m: Record<number, number>) => void;
  onAmsMapping: (m: number[] | undefined) => void;
}) {
  const { t } = useTranslation();
  const reqs = useQuery({
    queryKey: ['farm', 'plate-filaments', plateId, color],
    queryFn: () => farmApi.getPlateFilaments(plateId, color),
  });
  const status = useQuery({ queryKey: ['printer-status', printerId], queryFn: () => api.getPrinterStatus(printerId) });
  const assignments = useQuery({ queryKey: ['spool-assignments', printerId], queryFn: () => api.getAssignments(printerId) });
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.getSettings });
  // The filaments the sliced plate uses, in the component's colour: one 3MF serves every colour variant.
  const filamentReqs = useMemo(
    () => reqs.data && ({
      filaments: reqs.data.filaments.map((f) => ({ ...f, used_meters: f.used_meters ?? 0, tray_info_idx: '',
        color: colorHex ? `#${colorHex}` : f.color })),
    } as FilamentReqsData),
    [reqs.data, colorHex],
  );
  const { amsMapping } = useFilamentMapping(filamentReqs, status.data, mappings, settings.data?.prefer_lowest_filament,
    assignments.data, printerId);
  const manual = Object.keys(mappings).length > 0;
  const key = manual ? JSON.stringify(amsMapping) : '';
  useEffect(() => onAmsMapping(manual ? amsMapping : undefined), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="space-y-2">
      <Loaded printerId={printerId} />
      {reqs.data && !reqs.data.sliced && <p className="text-xs text-bambu-gray">{t('farm.jobs.slotsAfterSlice')}</p>}
      {reqs.data?.sliced && (
        <>
          <p className="text-xs text-bambu-gray">{t('farm.jobs.slotsFor', { plate: plateLabel })}</p>
          <FilamentMapping printerId={printerId} filamentReqs={filamentReqs} manualMappings={mappings}
            onManualMappingChange={onMappings} defaultExpanded
            currencySymbol={getCurrencySymbol(settings.data?.currency || 'USD')}
            defaultCostPerKg={settings.data?.default_filament_cost ?? 0} />
          {manual && <p className="text-xs text-bambu-gray">{t('farm.jobs.slotsChosen')}</p>}
        </>
      )}
    </div>
  );
}
