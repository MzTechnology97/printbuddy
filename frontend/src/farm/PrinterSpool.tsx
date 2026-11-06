// Printfarm (fork): the spool set by hand on a Klipper printer without CFS, on its card in Home. Such a printer
// can't say what is loaded: the user does, until they change it; Accoda uses it to pick the 3MF and check the colour.
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Pencil, X } from 'lucide-react';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type FarmPrinterSpool } from './api';
import './i18n';

const INPUT =
  'bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';
const MATERIALS = ['PLA', 'PETG', 'ASA', 'ABS', 'TPU', 'PA', 'PC'];

function SpoolEditor({ s, onDone }: { s: FarmPrinterSpool; onDone: () => void }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ['farm', 'plan-settings'], queryFn: farmApi.getPlanSettings });
  const colors = Object.keys(settings.data?.colors || {}).map((c) => c.charAt(0).toUpperCase() + c.slice(1));
  const [material, setMaterial] = useState(s.material || '');
  const [color, setColor] = useState(s.color_name || '');
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ['farm'] });
    onDone();
  };
  const save = useMutation({
    mutationFn: () => farmApi.setPrinterSpool(s.printer_id, { material, color_name: color }),
    onSuccess: () => { showToast(t('farm.products.saved'), 'success'); done(); },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const clear = useMutation({
    mutationFn: () => farmApi.clearPrinterSpool(s.printer_id),
    onSuccess: done,
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  return (
    <div className="space-y-3">
      <p className="text-xs text-bambu-gray">{t('farm.spools.intro')}</p>
      <div className="flex flex-wrap items-center gap-2">
        <input list={`farm-materials-${s.printer_id}`} aria-label={t('farm.spools.material')} className={`${INPUT} py-1 w-32`}
          value={material} placeholder={t('farm.spools.material')} onChange={(e) => setMaterial(e.target.value)} />
        <datalist id={`farm-materials-${s.printer_id}`}>
          {(s.materials?.length ? s.materials : MATERIALS).map((m) => <option key={m} value={m} />)}
        </datalist>
        <select aria-label={t('farm.spools.color')} className={`${INPUT} py-1`} value={color}
          onChange={(e) => setColor(e.target.value)}>
          <option value="">{t('farm.spools.color')}</option>
          {colors.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <div className="flex flex-wrap gap-2 justify-end">
        {s.material && (
          <Button size="sm" variant="ghost" disabled={clear.isPending} onClick={() => clear.mutate()}>
            {t('farm.spools.empty')}
          </Button>
        )}
        <Button size="sm" disabled={!material.trim() || !color || save.isPending} onClick={() => save.mutate()}>
          {t('farm.products.save')}
        </Button>
      </div>
    </div>
  );
}

/** The spool line of a printer's card: shown only for the printers that need it (``/farm/printer-spools``). */
export function PrinterSpoolLine({ printerId }: { printerId: number }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const spools = useQuery({
    queryKey: ['farm', 'printer-spools'],
    queryFn: farmApi.getPrinterSpools,
    staleTime: 30_000, // one request for all the cards
  });
  const s = spools.data?.find((p) => p.printer_id === printerId);
  if (!s) return null; // a CFS, an AMS or a printer that reports its filament: nothing to set by hand
  const label = s.material ? [s.material, s.color_name].filter(Boolean).join(' · ') : t('farm.spools.none');
  return (
    <div className="mt-2">
      <button type="button" onClick={() => setOpen(true)}
        aria-label={`${t('farm.spools.cardLabel')}: ${label}`}
        className="w-full flex items-center justify-between gap-2 rounded-lg bg-bambu-dark/70 border border-bambu-dark-tertiary px-3 py-2 text-left hover:border-bambu-green">
        <span className="min-w-0">
          <span className="block text-[10px] uppercase tracking-wide text-bambu-gray">{t('farm.spools.cardLabel')}</span>
          <span className={`flex items-center gap-1.5 text-xs ${s.material ? 'text-white' : 'text-amber-400'}`}>
            {s.color && (
              <span aria-hidden className="w-2.5 h-2.5 rounded-full border border-black/30 shrink-0"
                style={{ background: `#${s.color}` }} />
            )}
            <span className="truncate">{label}</span>
          </span>
        </span>
        <Pencil className="w-3.5 h-3.5 text-bambu-gray shrink-0" />
      </button>
      {open && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={() => setOpen(false)}>
          <div role="dialog" aria-label={`${t('farm.spools.title')} · ${s.name}`}
            className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg w-full max-w-md p-4 space-y-3"
            onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-white font-semibold">{t('farm.spools.title')} · {s.name}</h3>
              <button type="button" aria-label={t('farm.products.cancel')} onClick={() => setOpen(false)}
                className="p-1 rounded hover:bg-bambu-dark">
                <X className="w-5 h-5 text-bambu-gray" />
              </button>
            </div>
            <SpoolEditor s={s} onDone={() => setOpen(false)} />
          </div>
        </div>
      )}
    </div>
  );
}
