// Printfarm (fork): on the project page of a product, the farm view replaces upstream's
// Production Plan and BOM (which list every file as a part and have no quantities for products).
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, FileBox, ListPlus, Loader2, Package } from 'lucide-react';
import { Button } from '../components/Button';
import { Card, CardContent } from '../components/Card';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type FarmPlanComponent, type FarmProductView } from './api';
import { AccodaSummary } from './PrintPlanPage';
import { ProductPiecesPanel } from './ProductPiecesPanel';
import './i18n';

const duration = (s: number | null) => (s ? `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m` : null);
const fileName = (path: string) => path.split(/[\\/]/).pop() || path;

function FarmProductPanel({ view }: { view: FarmProductView }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [printing, setPrinting] = useState<FarmPlanComponent[] | null>(null);
  const plan = useMutation({
    mutationFn: () => farmApi.getProductPlan(view.project_id),
    onSuccess: (p) => setPrinting(p.components),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const exclude = useMutation({
    mutationFn: ({ id, excluded }: { id: number; excluded: boolean }) =>
      farmApi.setFileExcluded(view.project_id, id, excluded),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['farm'] }),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  return (
    <Card>
      <CardContent className="p-4 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <Package className="w-5 h-5 text-bambu-green" /> {t('farm.project.title')}
          </h2>
          <Link to="/farm/products" className="text-sm text-bambu-green hover:underline">
            {t('farm.project.openProducts')}
          </Link>
        </div>
        {(view.skus.length > 0 || view.asins.length > 0) && (
          <p className="text-xs text-bambu-gray break-words">
            {view.skus.join(' · ')}
            {view.asins.length > 0 && ` — ASIN ${view.asins.join(' · ')}`}
          </p>
        )}

        <ProductPiecesPanel projectId={view.project_id} />

        <section className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-white">{t('farm.project.parts')}</h3>
            <Button size="sm" disabled={view.parts.length === 0 || plan.isPending} title={t('farm.project.printNowHint')}
              onClick={() => plan.mutate()}>
              {plan.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <ListPlus className="w-4 h-4" />}
              {t('farm.project.printNow')}
            </Button>
          </div>
          {printing && (
            <AccodaSummary components={printing} byHand onClose={() => setPrinting(null)} onDone={() => undefined} />
          )}
          {view.parts.length === 0 && <p className="text-sm text-bambu-gray">{t('farm.products.noParts')}</p>}
          <ul className="space-y-3">
            {view.parts.map((part) => (
              <li key={part.code} className="bg-bambu-dark rounded-lg p-3 space-y-2">
                <p className="text-white">
                  {part.quantity}× <span className="font-mono text-bambu-green">{part.code}</span> {part.name}
                </p>
                {part.plates.length === 0 ? (
                  <p className="text-xs text-amber-400 flex items-center gap-1">
                    <AlertTriangle className="w-3.5 h-3.5" /> {t('farm.components.noPlateYet')}
                  </p>
                ) : (
                  <ul className="space-y-1">
                    {part.plates.map((p) => (
                      <li key={p.plate_id} className="text-xs text-bambu-gray break-all">
                        <span className="text-white">{fileName(p.file)}</span> ·{' '}
                        {t('farm.plates.plate', { index: p.plate })} ·{' '}
                        {[
                          p.printer_model,
                          p.material,
                          t('farm.project.yield', { count: p.yield }),
                          duration(p.print_time_seconds),
                          p.filament_grams ? `${Math.round(p.filament_grams)} g` : null,
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                        {!p.sliced && <span className="text-amber-400"> · {t('farm.project.notSliced')}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </section>

        <section className="space-y-2">
          <h3 className="text-sm font-semibold text-white">{t('farm.project.files')}</h3>
          {view.files.length === 0 ? (
            <p className="text-sm text-bambu-gray">{t('farm.project.noFiles')}</p>
          ) : (
            <ul className="divide-y divide-bambu-dark-tertiary">
              {view.files.map((f) => (
                <li key={f.library_file_id}
                  className={`py-2 flex flex-wrap items-center gap-2 text-sm ${f.excluded ? 'opacity-50' : ''}`}
                  title={f.excluded ? t('farm.project.excludeHint') : undefined}>
                  <FileBox className="w-4 h-4 text-bambu-gray shrink-0" />
                  <span className="min-w-0 flex-1 break-all">
                    <span className={f.excluded ? 'text-bambu-gray line-through' : 'text-white'}>{f.filename}</span>
                    <span className="text-xs text-bambu-gray block">{f.folder}</span>
                  </span>
                  {f.excluded ? (
                    <span className="text-xs text-bambu-gray">{t('farm.project.excluded')}</span>
                  ) : (
                    <span className="text-xs text-bambu-gray">
                      {f.opened
                        ? t('farm.plates.counts', { todo: f.proposed, done: f.confirmed })
                        : t('farm.plates.notOpened')}
                    </span>
                  )}
                  <Button size="sm" variant="secondary" disabled={exclude.isPending}
                    aria-label={`${t(f.excluded ? 'farm.project.include' : 'farm.project.exclude')} ${f.filename}`}
                    onClick={() => exclude.mutate({ id: f.library_file_id, excluded: !f.excluded })}>
                    {t(f.excluded ? 'farm.project.include' : 'farm.project.exclude')}
                  </Button>
                  {!f.excluded && (!f.opened || f.proposed > 0) && (
                    <Link
                      to={`/farm/products?tab=plates&q=${encodeURIComponent(f.filename)}`}
                      className="text-xs text-bambu-green hover:underline"
                    >
                      {t('farm.project.confirmPlates')}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

/** Renders ``children`` (upstream panels) for normal projects; for a product, the farm view (``panel``) or nothing. */
export function FarmProductGate({ projectId, panel = false, children }: {
  projectId: number;
  panel?: boolean;
  children: ReactNode;
}) {
  const view = useQuery({
    queryKey: ['farm', 'product-view', projectId],
    queryFn: () => farmApi.getProductView(projectId).catch(() => null), // 404: not a product
    enabled: projectId > 0,
  });
  if (view.isLoading) return null;
  if (!view.data) return <>{children}</>;
  return panel ? <FarmProductPanel view={view.data} /> : null;
}
