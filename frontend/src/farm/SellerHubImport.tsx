// Printfarm (fork): the Seller Hub listings without a product, picked by hand and imported as products.
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Loader2, Package, X } from 'lucide-react';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type SellerHubListing } from './api';
import './i18n';

function Photo({ row }: { row: SellerHubListing }) {
  const [broken, setBroken] = useState(false);
  return (
    <div className="w-14 h-14 shrink-0 rounded bg-white overflow-hidden flex items-center justify-center">
      {row.image_url && !broken ? (
        <img
          alt=""
          loading="lazy"
          decoding="async"
          width={56}
          height={56}
          className="w-full h-full object-contain"
          src={farmApi.sellerHubThumbnailUrl(row.image_url)}
          onError={() => setBroken(true)}
        />
      ) : (
        <Package className="w-6 h-6 text-bambu-gray/60" aria-hidden />
      )}
    </div>
  );
}

export function SellerHubImport({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ['farm', 'sellerhub-listings'],
    queryFn: farmApi.getSellerHubListings,
    retry: false,
  });
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const rows = list.data?.listings ?? [];
  // Only rows still on the list count: one imported elsewhere in the meantime drops out.
  const picked = rows.filter((r) => chosen.has(r.key)).map((r) => r.key);
  const all = rows.length > 0 && picked.length === rows.length;

  const toggle = (key: string) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const run = useMutation({
    mutationFn: () => farmApi.importSellerHubListings(picked),
    onSuccess: (res) => {
      const skipped = res.already_owned.length + res.not_found.length;
      showToast(
        [t('farm.products.sellerHub.done', { count: res.created.length }), skipped && t('farm.products.sellerHub.skipped', { count: skipped })]
          .filter(Boolean)
          .join(' · '),
        res.created.length ? 'success' : 'info',
      );
      setChosen(new Set());
      queryClient.invalidateQueries({ queryKey: ['farm'] });
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  return (
    <section
      aria-labelledby="farm-sellerhub-title"
      className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg p-4 space-y-3"
    >
      <div className="flex items-start justify-between gap-3">
        <h2 id="farm-sellerhub-title" className="text-white font-semibold">
          {t('farm.products.sellerHub.open')}
        </h2>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label={t('farm.products.sellerHub.close')}>
          <X className="w-4 h-4" />
        </Button>
      </div>
      <p className="text-sm text-bambu-gray max-w-3xl">{t('farm.products.sellerHub.intro')}</p>

      {list.isLoading ? (
        <p className="flex items-center gap-2 text-sm text-bambu-gray">
          <Loader2 className="w-4 h-4 animate-spin" /> {t('farm.products.sellerHub.loading')}
        </p>
      ) : list.error ? (
        <p className="text-red-400 text-sm">{(list.error as Error).message}</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-bambu-gray">{t('farm.products.sellerHub.empty')}</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-sm text-white">
              <input
                type="checkbox"
                checked={all}
                onChange={() => setChosen(all ? new Set() : new Set(rows.map((r) => r.key)))}
              />
              {t('farm.products.sellerHub.selectAll', { count: rows.length })}
            </label>
            <span className="text-sm text-bambu-gray" aria-live="polite">
              {t('farm.products.sellerHub.selected', { count: picked.length })}
            </span>
          </div>
          <ul className="divide-y divide-bambu-dark-tertiary border border-bambu-dark-tertiary rounded">
            {rows.map((r) => (
              <li key={r.key}>
                <label className="flex items-center gap-3 p-2 cursor-pointer hover:bg-bambu-dark">
                  <input type="checkbox" checked={chosen.has(r.key)} onChange={() => toggle(r.key)} />
                  <Photo row={r} />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm text-white break-words">{r.title || r.name}</span>
                    <span className="block text-xs text-bambu-gray font-mono break-words">{r.skus.join(' · ')}</span>
                    {r.title && r.name !== r.title && (
                      <span className="block text-xs text-bambu-gray break-words">
                        {t('farm.products.sellerHub.becomes', { name: r.name })}
                      </span>
                    )}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </>
      )}

      {list.data && (list.data.excluded_inactive > 0 || list.data.ignored > 0) && (
        <p className="text-xs text-bambu-gray">
          {t('farm.products.sellerHub.excluded', { inactive: list.data.excluded_inactive, ignored: list.data.ignored })}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <Button onClick={() => run.mutate()} disabled={picked.length === 0 || run.isPending}>
          {run.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
          {t('farm.products.sellerHub.import', { count: picked.length })}
        </Button>
        <Button variant="ghost" onClick={onClose}>
          {t('farm.products.sellerHub.close')}
        </Button>
      </div>
    </section>
  );
}
