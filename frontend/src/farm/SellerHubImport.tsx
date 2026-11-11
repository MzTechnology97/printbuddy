// Printfarm (fork): the Seller Hub listings without a product, picked by hand and imported as products.
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Loader2, Package, X } from 'lucide-react';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type SellerHubListing } from './api';
import './i18n';

const LISTINGS_KEY = ['farm', 'sellerhub-listings'];
/** Light check while the Products page is open: Seller Hub's plan is read again every 5 minutes. */
const CHECK_EVERY_MS = 5 * 60 * 1000;

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

/** Title, SKUs and (when the product gets another name) the name it will have. */
function Texts({ row }: { row: SellerHubListing }) {
  const { t } = useTranslation();
  return (
    <span className="min-w-0 flex-1">
      <span className="block text-sm text-white break-words">{row.title || row.name}</span>
      <span className="block text-xs text-bambu-gray font-mono break-words">{row.skus.join(' · ')}</span>
      {row.title && row.name !== row.title && (
        <span className="block text-xs text-bambu-gray break-words">
          {t('farm.products.sellerHub.becomes', { name: row.name })}
        </span>
      )}
    </span>
  );
}

/** The listings without a product that "Ignore" was not pressed on: what the new-products window offers. */
function useNewListings(): SellerHubListing[] {
  const list = useQuery({
    queryKey: LISTINGS_KEY,
    queryFn: farmApi.getSellerHubListings,
    retry: false,
    refetchInterval: CHECK_EVERY_MS,
  });
  return (list.data?.listings ?? []).filter((r) => !r.dismissed);
}

function useImport() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (keys: string[]) => farmApi.importSellerHubListings(keys),
    onSuccess: (res) => {
      const skipped = res.already_owned.length + res.not_found.length;
      showToast(
        [t('farm.products.sellerHub.done', { count: res.created.length }), skipped && t('farm.products.sellerHub.skipped', { count: skipped })]
          .filter(Boolean)
          .join(' · '),
        res.created.length ? 'success' : 'info',
      );
      queryClient.invalidateQueries({ queryKey: ['farm'] });
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
}

export function SellerHubImport({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const list = useQuery({ queryKey: LISTINGS_KEY, queryFn: farmApi.getSellerHubListings, retry: false });
  const run = useImport();
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
                  <Texts row={r} />
                  {r.dismissed && (
                    <span className="text-xs text-bambu-gray shrink-0" title={t('farm.products.sellerHub.dismissedNote')}>
                      {t('farm.products.sellerHub.dismissed')}
                    </span>
                  )}
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
        <Button
          onClick={() => run.mutate(picked, { onSuccess: () => setChosen(new Set()) })}
          disabled={picked.length === 0 || run.isPending}
        >
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

/**
 * "New product found on Seller Hub": open by itself while there are new listings (when the page opens and when the
 * periodic check finds one). Closed with X it stays closed for this visit, unless another new listing appears.
 */
export function NewSellerHubProducts() {
  const { t } = useTranslation();
  const rows = useNewListings();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const run = useImport();
  const [closedOn, setClosedOn] = useState<Set<string> | null>(null);
  const ignore = useMutation({
    mutationFn: (keys: string[]) => farmApi.ignoreSellerHubListings(keys),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: LISTINGS_KEY }),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const busy = run.isPending || ignore.isPending;
  if (rows.length === 0 || (closedOn && rows.every((r) => closedOn.has(r.key)))) return null;
  const close = () => setClosedOn(new Set(rows.map((r) => r.key)));

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="farm-sellerhub-new-title"
        onKeyDown={(e) => e.key === 'Escape' && close()}
        className="bg-bambu-dark-secondary rounded-lg w-full max-w-xl max-h-[90vh] flex flex-col border border-bambu-dark-tertiary"
      >
        <div className="flex items-start justify-between gap-3 p-4 border-b border-bambu-dark-tertiary">
          <h2 id="farm-sellerhub-new-title" className="text-white font-semibold">
            {t('farm.products.sellerHub.newTitle', { count: rows.length })}
          </h2>
          <Button variant="ghost" size="sm" onClick={close} aria-label={t('farm.products.sellerHub.close')}>
            <X className="w-4 h-4" />
          </Button>
        </div>
        <ul className="overflow-y-auto divide-y divide-bambu-dark-tertiary">
          {rows.map((r) => (
            <li key={r.key} className="flex flex-wrap items-center gap-3 p-3">
              <Photo row={r} />
              <Texts row={r} />
              <div className="flex gap-2 shrink-0">
                <Button size="sm" disabled={busy} onClick={() => run.mutate([r.key])}
                  aria-label={t('farm.products.sellerHub.addOne', { name: r.title || r.name })}>
                  {t('farm.products.sellerHub.add')}
                </Button>
                <Button size="sm" variant="secondary" disabled={busy} onClick={() => ignore.mutate([r.key])}
                  aria-label={t('farm.products.sellerHub.ignoreOne', { name: r.title || r.name })}>
                  {t('farm.products.sellerHub.ignore')}
                </Button>
              </div>
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap items-center justify-between gap-2 p-4 border-t border-bambu-dark-tertiary">
          <p className="text-xs text-bambu-gray">{t('farm.products.sellerHub.newNote')}</p>
          {rows.length > 1 && (
            <Button disabled={busy} onClick={() => run.mutate(rows.map((r) => r.key))}>
              {run.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
              {t('farm.products.sellerHub.addAll', { count: rows.length })}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

/** The number of new listings, on the "Import from Seller Hub" button. */
export function NewSellerHubBadge() {
  const { t } = useTranslation();
  const count = useNewListings().length;
  if (count === 0) return null;
  const label = t('farm.products.sellerHub.newBadge', { count });
  return (
    <span className="ml-1 min-w-5 px-1.5 rounded-full bg-amber-500 text-bambu-dark text-xs font-semibold"
      title={label} aria-label={label}>
      {count}
    </span>
  );
}
