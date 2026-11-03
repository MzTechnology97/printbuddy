// Printfarm (fork): prints done outside Accoda that Printbuddy could not recognise. The user says which
// components and how many pieces they printed (then they become batches for Seller Hub), or that they are
// not production. See backend/app/farm/jobs/outside.py.
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type FarmJob } from './api';
import './i18n';

const INPUT =
  'bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';

function AssignRow({ job }: { job: FarmJob }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const products = useQuery({ queryKey: ['farm', 'products'], queryFn: farmApi.getProducts });
  const proposed = new Map((job.proposal || []).map((p) => [p.component_code, p.pieces]));
  const guess = products.data?.products.find((p) => p.parts.some((part) => proposed.has(part.component_code)));
  const [productId, setProductId] = useState<number | null>(null);
  const [pieces, setPieces] = useState<Record<string, string>>({});
  const product = products.data?.products.find((p) => p.project_id === (productId ?? guess?.project_id));
  const value = (code: string) => pieces[code] ?? (proposed.has(code) ? String(proposed.get(code)) : '');
  const parts = (product?.parts || [])
    .map((p) => ({ component_code: p.component_code, pieces: Number(value(p.component_code)) || 0 }))
    .filter((p) => p.pieces > 0);

  const assign = useMutation({
    mutationFn: (body: Parameters<typeof farmApi.assignJob>[1]) => farmApi.assignJob(job.id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['farm'] }),
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  return (
    <li className="p-3 space-y-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-white break-all">{job.print_name}</span>
        {job.printer && <span className="text-bambu-gray">· {job.printer}</span>}
        {job.finished_at && (
          <span className="text-bambu-gray">· {new Date(`${job.finished_at}Z`).toLocaleString()}</span>
        )}
      </div>
      {job.proposal?.length ? (
        <p className="text-xs text-bambu-gray">
          {t('farm.outside.proposal', {
            parts: job.proposal.map((p) => `${p.component_code} ${p.name} ×${p.pieces}`).join(', '),
          })}
        </p>
      ) : null}
      <select aria-label={`${t('farm.outside.product')} ${job.print_name}`} className={INPUT}
        value={product?.project_id ?? ''} onChange={(e) => setProductId(Number(e.target.value) || null)}>
        <option value="">{t('farm.outside.pickProduct')}</option>
        {(products.data?.products || []).map((p) => (
          <option key={p.project_id} value={p.project_id}>{p.name}</option>
        ))}
      </select>
      {product && (
        <ul className="space-y-1">
          {product.parts.map((p) => (
            <li key={p.component_code} className="flex flex-wrap items-center gap-2">
              <input aria-label={`${t('farm.outside.pieces')} ${p.component_code}`} type="number" min={0}
                className={`${INPUT} w-20`} value={value(p.component_code)}
                onChange={(e) => setPieces({ ...pieces, [p.component_code]: e.target.value })} />
              <span className="font-mono text-bambu-green">{p.component_code}</span>
              <span className="text-bambu-gray break-all">{p.name}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={parts.length === 0 || assign.isPending} onClick={() => assign.mutate({ parts })}>
          {t('farm.outside.assign')}
        </Button>
        <Button size="sm" variant="secondary" disabled={assign.isPending}
          onClick={() => assign.mutate({ not_production: true })}>
          {t('farm.outside.notProduction')}
        </Button>
      </div>
    </li>
  );
}

export function OutsidePrints({ jobs }: { jobs: FarmJob[] }) {
  const { t } = useTranslation();
  const todo = jobs.filter((j) => j.state === 'to_assign');
  if (!todo.length) return null;
  return (
    <section className="space-y-2">
      <h2 className="text-lg font-semibold text-white">{t('farm.outside.title', { count: todo.length })}</h2>
      <p className="text-xs text-bambu-gray max-w-3xl">{t('farm.outside.intro')}</p>
      <ul className="divide-y divide-bambu-dark-tertiary bg-bambu-dark-secondary border border-amber-500/40 rounded-lg">
        {todo.map((j) => <AssignRow key={j.id} job={j} />)}
      </ul>
    </section>
  );
}
