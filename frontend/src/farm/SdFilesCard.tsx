// Fork (printfarm): Bambu printers that keep the file on their storage (SD or USB stick) after a print,
// so it can be reprinted from the display. In Settings; one setting (GET/PUT /farm/sd-files) for every
// file sent to the printer: queue, Accoda, archive reprints, prints outside the queue.
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { HardDrive } from 'lucide-react';
import { Card, CardContent, CardHeader } from '../components/Card';
import { Button } from '../components/Button';
import { Toggle } from '../components/Toggle';
import { useToast } from '../contexts/ToastContext';
import { farmApi } from './api';
import './i18n';

export function SdFilesCard() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const data = useQuery({ queryKey: ['farm', 'sd-files'], queryFn: farmApi.getSdFiles }).data;
  const [days, setDays] = useState(14);
  const [kept, setKept] = useState<Set<number>>(new Set());
  useEffect(() => {
    if (!data) return;
    setDays(data.keep_days);
    setKept(new Set(data.printers.filter((p) => p.keep).map((p) => p.id)));
  }, [data]);
  const save = useMutation({
    mutationFn: () => farmApi.saveSdFiles({ keep_days: days, keep_printer_ids: [...kept] }),
    onSuccess: (saved) => {
      queryClient.setQueryData(['farm', 'sd-files'], saved);
      showToast(t('farm.products.saved'), 'success');
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  if (!data?.printers.length) return null;
  const toggle = (id: number, on: boolean) => {
    const next = new Set(kept);
    if (on) next.add(id);
    else next.delete(id);
    setKept(next);
  };
  return (
    <Card id="card-sd-files">
      <CardHeader>
        <h3 className="text-base font-semibold text-white flex items-center gap-2">
          <HardDrive className="w-4 h-4 text-bambu-green" /> {t('farm.sdFiles.title')}
        </h3>
      </CardHeader>
      <CardContent>
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <p className="text-xs text-bambu-gray">{t('farm.sdFiles.intro')}</p>
          <ul className="space-y-3">
            {data.printers.map((p) => (
              <li key={p.id} className="text-sm text-white">
                {/* The label names the switch: printer and what it does. */}
                <label className="flex items-start justify-between gap-3">
                  <span>
                    {p.name} <span className="text-xs text-bambu-gray">{p.model}</span>
                    <span className="block text-xs text-bambu-gray">{t('farm.sdFiles.keep')}</span>
                  </span>
                  <Toggle checked={kept.has(p.id)} onChange={(on) => toggle(p.id, on)} />
                </label>
                {p.ghost_print_risk && kept.has(p.id) && (
                  <p role="alert" className="text-xs text-amber-400 mt-1">{t('farm.sdFiles.ghostRisk')}</p>
                )}
              </li>
            ))}
          </ul>
          <label className="flex items-center gap-2 text-sm text-white">
            {t('farm.sdFiles.days')}
            <input type="number" min={1} max={365} value={days} onChange={(e) => setDays(Number(e.target.value))}
              className="w-20 px-3 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green" />
          </label>
          <div className="flex justify-end">
            <Button type="submit" disabled={save.isPending || !(days >= 1 && days <= 365)}>{t('farm.products.save')}</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
