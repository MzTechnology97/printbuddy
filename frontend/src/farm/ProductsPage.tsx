// Printfarm (fork) products: projects with Amazon SKUs and printed parts, read by Seller Hub.
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Package, Plus, Trash2, Loader2, Search, Layers, Grid3x3 } from 'lucide-react';
import { api } from '../api/client';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type FarmComponent, type FarmProduct, type FarmProductInput, type FarmShape } from './api';
import { ComponentsPanel } from './ComponentsPanel';
import { PlatesPanel } from './PlatesPanel';
import './i18n';

const INPUT =
  'w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';
const LABEL = 'block text-sm font-medium text-white mb-1';

interface Draft {
  id?: number;
  projectId?: number;
  name: string;
  skus: string;
  asins: string;
  parts: { code: string; quantity: number }[];
}

const splitList = (value: string) => value.split(',').map((s) => s.trim()).filter(Boolean);

/** Every word must appear in the name, a SKU, an ASIN or a part (code or name). */
function matches(p: FarmProduct, query: string): boolean {
  const text = [p.name, ...p.skus, ...p.asins, ...p.parts.flatMap((x) => [x.component_code, x.name])]
    .join(' ')
    .toLowerCase();
  return query.toLowerCase().split(/\s+/).every((word) => text.includes(word));
}

function draftOf(p: FarmProduct): Draft {
  return {
    id: p.project_id,
    name: p.name,
    skus: p.skus.join(', '),
    asins: p.asins.join(', '),
    parts: p.parts.map((x) => ({ code: x.component_code, quantity: x.quantity })),
  };
}

function ProductForm({
  draft,
  components,
  shapes,
  onDone,
}: {
  draft: Draft;
  components: FarmComponent[];
  shapes: FarmShape[];
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [d, setD] = useState(draft);
  const [newComponent, setNewComponent] = useState({ name: '', shapeId: '', color: '' });
  const { data: projects } = useQuery({ queryKey: ['farm', 'project-names'], queryFn: farmApi.getProjectNames, enabled: !d.id });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['farm'] });

  const save = useMutation({
    mutationFn: () => {
      const body: FarmProductInput = {
        name: d.name || undefined,
        skus: splitList(d.skus),
        asins: splitList(d.asins),
        parts: d.parts.filter((p) => p.code).map((p) => ({ component_code: p.code, quantity: p.quantity })),
      };
      if (d.id) return farmApi.updateProduct(d.id, { ...body, name: d.name });
      return farmApi.createProduct({ ...body, project_id: d.projectId });
    },
    onSuccess: () => {
      refresh();
      showToast(t('farm.products.saved'), 'success');
      onDone();
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const unmark = useMutation({
    mutationFn: () => farmApi.unmarkProduct(d.id!),
    onSuccess: () => {
      refresh();
      onDone();
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const addComponent = useMutation({
    mutationFn: () =>
      farmApi.createComponent({
        name: newComponent.name.trim() || undefined,
        shape_id: newComponent.shapeId ? Number(newComponent.shapeId) : undefined,
        color: newComponent.color.trim() || undefined,
      }),
    onSuccess: (c) => {
      refresh();
      setNewComponent({ name: '', shapeId: '', color: '' });
      setD({ ...d, parts: [...d.parts, { code: c.code, quantity: 1 }] });
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const uploadCover = async (file: File | undefined) => {
    if (!file || !d.id) return;
    try {
      await api.uploadProjectCoverImage(d.id, file);
      refresh();
    } catch (e) {
      showToast((e as Error).message, 'error');
    }
  };

  const setPart = (i: number, patch: Partial<Draft['parts'][number]>) =>
    setD({ ...d, parts: d.parts.map((p, j) => (j === i ? { ...p, ...patch } : p)) });

  return (
    <form
      className="space-y-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg p-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      {!d.id && (
        <div>
          <label className={LABEL} htmlFor="farm-from-project">{t('farm.products.fromProject')}</label>
          <select
            id="farm-from-project"
            className={INPUT}
            value={d.projectId ?? ''}
            onChange={(e) => setD({ ...d, projectId: e.target.value ? Number(e.target.value) : undefined })}
          >
            <option value="">{t('farm.products.newProject')}</option>
            {(projects || []).map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
      )}
      <div>
        <label className={LABEL} htmlFor="farm-name">{t('farm.products.name')}</label>
        <input id="farm-name" className={INPUT} value={d.name} required={!d.projectId} maxLength={255}
          onChange={(e) => setD({ ...d, name: e.target.value })} />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className={LABEL} htmlFor="farm-skus">{t('farm.products.skus')}</label>
          <input id="farm-skus" className={INPUT} value={d.skus} placeholder={t('farm.products.listHint')}
            onChange={(e) => setD({ ...d, skus: e.target.value })} />
        </div>
        <div>
          <label className={LABEL} htmlFor="farm-asins">{t('farm.products.asins')}</label>
          <input id="farm-asins" className={INPUT} value={d.asins} placeholder={t('farm.products.listHint')}
            onChange={(e) => setD({ ...d, asins: e.target.value })} />
        </div>
      </div>
      <div>
        <span className={LABEL}>{t('farm.products.parts')}</span>
        <div className="space-y-2">
          {d.parts.map((p, i) => (
            <div key={i} className="flex gap-2 items-center">
              <select aria-label={t('farm.products.pickComponent')} className={INPUT} value={p.code}
                onChange={(e) => setPart(i, { code: e.target.value })}>
                <option value="">{t('farm.products.pickComponent')}</option>
                {components.filter((c) => !c.retired || c.code === p.code).map((c) => (
                  <option key={c.code} value={c.code}>{c.code} · {c.name}</option>
                ))}
              </select>
              <input aria-label={t('farm.products.quantity')} type="number" min={1} max={1000} className={`${INPUT} w-24`}
                value={p.quantity} onChange={(e) => setPart(i, { quantity: Number(e.target.value) })} />
              <Button type="button" variant="ghost" size="sm" aria-label={t('farm.products.remove')}
                onClick={() => setD({ ...d, parts: d.parts.filter((_, j) => j !== i) })}>
                <Trash2 className="w-4 h-4" />
              </Button>
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" size="sm"
              onClick={() => setD({ ...d, parts: [...d.parts, { code: '', quantity: 1 }] })}>
              <Plus className="w-4 h-4" /> {t('farm.products.addPart')}
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            <select aria-label={t('farm.products.shape')} className={`${INPUT} md:w-56`} value={newComponent.shapeId}
              onChange={(e) => setNewComponent({ ...newComponent, shapeId: e.target.value })}>
              <option value="">{t('farm.products.noShape')}</option>
              {shapes.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
            <input aria-label={t('farm.products.color')} className={`${INPUT} md:w-32`} value={newComponent.color}
              placeholder={t('farm.products.color')} maxLength={50}
              onChange={(e) => setNewComponent({ ...newComponent, color: e.target.value })} />
            <input aria-label={t('farm.products.newComponent')} className={`${INPUT} md:w-64`} value={newComponent.name}
              placeholder={t('farm.products.newComponent')} maxLength={200}
              onChange={(e) => setNewComponent({ ...newComponent, name: e.target.value })} />
            <Button type="button" variant="secondary" size="sm"
              disabled={!(newComponent.name.trim() || (newComponent.shapeId && newComponent.color.trim())) || addComponent.isPending}
              onClick={() => addComponent.mutate()}>
              {t('farm.products.createComponent')}
            </Button>
          </div>
        </div>
      </div>
      {d.id && (
        <div>
          <label className={LABEL} htmlFor="farm-cover">{t('farm.products.cover')}</label>
          <input id="farm-cover" type="file" accept="image/*" className="text-sm text-bambu-gray"
            onChange={(e) => uploadCover(e.target.files?.[0])} />
        </div>
      )}
      <div className="flex flex-wrap gap-2 justify-end">
        {d.id && (
          <Button type="button" variant="danger" className="mr-auto"
            onClick={() => window.confirm(t('farm.products.removeConfirm', { name: d.name })) && unmark.mutate()}>
            {t('farm.products.remove')}
          </Button>
        )}
        <Button type="button" variant="secondary" onClick={onDone}>{t('farm.products.cancel')}</Button>
        <Button type="submit" disabled={save.isPending}>{t('farm.products.save')}</Button>
      </div>
    </form>
  );
}

export function ProductsPage() {
  const { t } = useTranslation();
  const [editing, setEditing] = useState<Draft | null>(null);
  const [query, setQuery] = useState('');
  const [params, setParams] = useSearchParams();
  const tab = (['components', 'plates'] as const).find((id) => id === params.get('tab')) ?? 'products';
  const products = useQuery({ queryKey: ['farm', 'products'], queryFn: farmApi.getProducts });
  const overview = useQuery({ queryKey: ['farm', 'overview'], queryFn: farmApi.getOverview });
  const components = useQuery({ queryKey: ['farm', 'components'], queryFn: farmApi.getComponents });
  const shapes = useQuery({ queryKey: ['farm', 'shapes'], queryFn: farmApi.getShapes });
  const stats = new Map((overview.data || []).map((o) => [o.project_id, o]));
  const shown = (products.data?.products || []).filter((p) => matches(p, query.trim()));

  return (
    <div className="p-4 md:p-8 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-white flex items-center gap-3">
          <Package className="w-7 h-7 text-bambu-green" /> {t('farm.products.title')}
        </h1>
        {!editing && tab === 'products' && (
          <Button onClick={() => setEditing({ name: '', skus: '', asins: '', parts: [] })}>
            <Plus className="w-4 h-4" /> {t('farm.products.new')}
          </Button>
        )}
      </div>
      <div role="tablist" className="flex gap-2 border-b border-bambu-dark-tertiary">
        {(['products', 'components', 'plates'] as const).map((id) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => setParams(id === 'products' ? {} : { tab: id })}
            className={`flex items-center gap-2 px-4 py-2 -mb-px border-b-2 text-sm ${
              tab === id ? 'border-bambu-green text-white' : 'border-transparent text-bambu-gray hover:text-white'
            }`}
          >
            {{ products: <Package className="w-4 h-4" />, components: <Layers className="w-4 h-4" />, plates: <Grid3x3 className="w-4 h-4" /> }[id]}
            {t({ products: 'farm.products.title', components: 'farm.products.components', plates: 'farm.plates.title' }[id])}
          </button>
        ))}
      </div>
      {tab === 'components' ? (
        <ComponentsPanel />
      ) : tab === 'plates' ? (
        <PlatesPanel />
      ) : (
        <>
      <p className="text-bambu-gray text-sm max-w-3xl">{t('farm.products.intro')}</p>
      <div className="relative max-w-md">
        <Search className="w-4 h-4 text-bambu-gray absolute left-2.5 top-1/2 -translate-y-1/2" />
        <input
          type="search"
          aria-label={t('farm.products.search')}
          placeholder={t('farm.products.search')}
          className={`${INPUT} pl-8`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {editing && (
        <ProductForm key={editing.id ?? 'new'} draft={editing} components={components.data || []} shapes={shapes.data || []} onDone={() => setEditing(null)} />
      )}

      {products.isLoading ? (
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      ) : products.error ? (
        <p className="text-red-400">{(products.error as Error).message}</p>
      ) : shown.length === 0 ? (
        <p className="text-bambu-gray">{t(query.trim() ? 'farm.products.noMatch' : 'farm.products.empty')}</p>
      ) : (
        <ul className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {shown.map((p) => {
            const s = stats.get(p.project_id);
            return (
              <li key={p.project_id} className="flex gap-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg p-4">
                <div className="w-20 h-20 shrink-0 rounded bg-bambu-dark overflow-hidden flex items-center justify-center">
                  {s?.has_cover ? (
                    <img alt="" loading="lazy" decoding="async" className="w-full h-full object-cover"
                      src={`${api.getProjectCoverImageUrl(p.project_id)}${api.getProjectCoverImageUrl(p.project_id).includes('?') ? '&' : '?'}v=${encodeURIComponent(p.updated_at)}`} />
                  ) : (
                    <Package className="w-8 h-8 text-bambu-gray/50" />
                  )}
                </div>
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex items-start justify-between gap-2">
                    <h2 className="text-white font-semibold truncate">{p.name}</h2>
                    <Button variant="ghost" size="sm" onClick={() => setEditing(draftOf(p))}>{t('farm.products.edit')}</Button>
                  </div>
                  {(p.skus.length > 0 || p.asins.length > 0) && (
                    <p className="text-xs text-bambu-gray break-words">
                      {p.skus.join(' · ')}{p.asins.length > 0 && ` — ASIN ${p.asins.join(' · ')}`}
                    </p>
                  )}
                  <ul className="text-sm text-bambu-gray-light">
                    {p.parts.length === 0 && <li className="text-bambu-gray">{t('farm.products.noParts')}</li>}
                    {p.parts.map((part) => (
                      <li key={part.component_code}>
                        {part.quantity}× <span className="font-mono">{part.component_code}</span> {part.name}
                        <span className="text-xs text-bambu-gray">
                          {' · '}
                          {part.plates.length ? t('farm.products.plates', { count: part.plates.length }) : t('farm.products.noPlates')}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {s && (
                    <p className="text-xs text-bambu-gray">
                      {t('farm.products.queued', { count: s.queued })} · {t('farm.products.printing', { count: s.printing })} ·{' '}
                      {t('farm.products.printedRecent', { count: s.printed_recent })} ·{' '}
                      <Link to={`/projects/${p.project_id}`} className="text-bambu-green hover:underline">{t('farm.products.prints')}</Link>
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

        </>
      )}
    </div>
  );
}
