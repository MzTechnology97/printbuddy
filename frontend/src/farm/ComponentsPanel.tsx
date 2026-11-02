// Printfarm (fork): components grouped by shape, with their colours, plates and the products using them.
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Layers, Loader2, Pencil, Plus, Search } from 'lucide-react';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type FarmComponent } from './api';
import './i18n';

const INPUT =
  'bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';

// Colour names used in the farm -> swatch; anything else shows a striped dot.
const SWATCHES: Record<string, string> = {
  nero: '#111111',
  bianco: '#f5f5f5',
  grigio: '#8a8a8a',
  'marrone scuro': '#4a2c1a',
  marrone: '#6b4226',
  arancio: '#f28c28',
  giallo: '#f5d000',
  verde: '#2e9e44',
  rosso: '#d32f2f',
  blu: '#1e5bd8',
  beige: '#d8c3a5',
};

type Filter = 'all' | 'no-plate' | 'unused';

function Swatch({ color }: { color: string | null }) {
  const hex = color ? SWATCHES[color.toLowerCase()] : undefined;
  return (
    <span
      aria-hidden
      className="mt-0.5 w-4 h-4 rounded-full border border-bambu-gray/60 shrink-0"
      style={{ background: hex ?? 'repeating-linear-gradient(45deg,#555 0 3px,#333 3px 6px)' }}
    />
  );
}

function InlineName({ value, label, onSave }: { value: string; label: string; onSave: (name: string) => void }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<string | null>(null);
  if (draft === null) {
    return (
      <span className="flex items-center gap-1 min-w-0">
        <span className="break-words min-w-0">{value}</span>
        <button
          type="button"
          aria-label={`${t('farm.products.edit')} ${label}`}
          className="p-1 text-bambu-gray hover:text-white shrink-0"
          onClick={() => setDraft(value)}
        >
          <Pencil className="w-3.5 h-3.5" />
        </button>
      </span>
    );
  }
  return (
    <form
      className="flex gap-2 flex-1"
      onSubmit={(e) => {
        e.preventDefault();
        if (draft.trim() && draft.trim() !== value) onSave(draft.trim());
        setDraft(null);
      }}
    >
      <input aria-label={label} autoFocus className={`${INPUT} flex-1 py-1`} value={draft} maxLength={200}
        onChange={(e) => setDraft(e.target.value)} />
      <Button type="submit" size="sm">{t('farm.products.save')}</Button>
      <Button type="button" size="sm" variant="secondary" onClick={() => setDraft(null)}>
        {t('farm.products.cancel')}
      </Button>
    </form>
  );
}

export function ComponentsPanel() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [showRetired, setShowRetired] = useState(false);
  const [newShape, setNewShape] = useState('');
  const [newColor, setNewColor] = useState<Record<number, string>>({});

  const products = useQuery({ queryKey: ['farm', 'products'], queryFn: farmApi.getProducts });
  const components = useQuery({ queryKey: ['farm', 'components'], queryFn: farmApi.getComponents });
  const shapes = useQuery({ queryKey: ['farm', 'shapes'], queryFn: farmApi.getShapes });
  const plates = useQuery({ queryKey: ['farm', 'plates', 'confirmed'], queryFn: () => farmApi.getPlates('confirmed') });

  const onError = (e: Error) => showToast(e.message, 'error');
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['farm'] });
  const updateComponent = useMutation({
    mutationFn: ({ code, ...data }: { code: string; name?: string; retired?: boolean }) =>
      farmApi.updateComponent(code, data),
    onSuccess: refresh,
    onError,
  });
  const renameShape = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) => farmApi.renameShape(id, name),
    onSuccess: refresh,
    onError,
  });
  const createShape = useMutation({
    mutationFn: () => farmApi.createShape(newShape.trim()),
    onSuccess: () => {
      setNewShape('');
      refresh();
    },
    onError,
  });
  const createComponent = useMutation({
    mutationFn: ({ shapeId, color }: { shapeId: number; color: string }) =>
      farmApi.createComponent({ shape_id: shapeId, color }),
    onSuccess: (_, { shapeId }) => {
      setNewColor((prev) => ({ ...prev, [shapeId]: '' }));
      refresh();
    },
    onError,
  });

  // Products using each component, and confirmed plates per shape (or per old component code).
  const usage = useMemo(() => {
    const map = new Map<string, { name: string; quantity: number }[]>();
    for (const p of products.data?.products || []) {
      for (const part of p.parts) {
        map.set(part.component_code, [...(map.get(part.component_code) || []), { name: p.name, quantity: part.quantity }]);
      }
    }
    return map;
  }, [products.data]);
  const plateCount = useMemo(() => {
    const byShape = new Map<number, number>();
    const byCode = new Map<string, number>();
    for (const plate of plates.data || []) {
      for (const shapeId of new Set(plate.yields.map((y) => y.shape_id).filter((s): s is number => s != null))) {
        byShape.set(shapeId, (byShape.get(shapeId) || 0) + 1);
      }
      for (const code of new Set(plate.yields.map((y) => y.component_code).filter((c): c is string => !!c))) {
        byCode.set(code, (byCode.get(code) || 0) + 1);
      }
    }
    return { byShape, byCode };
  }, [plates.data]);

  const loading = products.isLoading || components.isLoading || shapes.isLoading;
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const platesOf = (c: FarmComponent) =>
    c.shape_id ? plateCount.byShape.get(c.shape_id) || 0 : plateCount.byCode.get(c.code) || 0;
  const visible = (c: FarmComponent, shapeName: string) => {
    if (c.retired && !showRetired) return false;
    if (filter === 'unused' && (usage.get(c.code) || []).length > 0) return false;
    if (filter === 'no-plate' && platesOf(c) > 0) return false;
    const text = [c.code, c.name, shapeName, c.color, ...(usage.get(c.code) || []).map((u) => u.name)]
      .join(' ')
      .toLowerCase();
    return words.every((w) => text.includes(w));
  };

  const groups = [
    ...(shapes.data || []).map((s) => ({ shape: s, items: (components.data || []).filter((c) => c.shape_id === s.id) })),
    { shape: null, items: (components.data || []).filter((c) => !c.shape_id) },
  ]
    .map((g) => ({ ...g, shown: g.items.filter((c) => visible(c, g.shape?.name ?? '')) }))
    // An empty new shape stays visible (to add its first colour) unless the user is searching or filtering.
    .filter((g) => g.shown.length > 0 || (g.shape && g.items.length === 0 && filter === 'all' && !words.length));

  const active = (components.data || []).filter((c) => !c.retired);
  const retiredCount = (components.data || []).length - active.length;

  return (
    <div className="space-y-4">
      <p className="text-bambu-gray text-sm max-w-3xl">{t('farm.products.componentsHint')}</p>
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[16rem] max-w-md">
          <Search className="w-4 h-4 text-bambu-gray absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input type="search" aria-label={t('farm.components.search')} placeholder={t('farm.components.search')}
            className={`${INPUT} w-full pl-8`} value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <select aria-label={t('farm.components.filter')} className={INPUT} value={filter}
          onChange={(e) => setFilter(e.target.value as Filter)}>
          <option value="all">{t('farm.components.all')}</option>
          <option value="no-plate">{t('farm.components.noPlate')}</option>
          <option value="unused">{t('farm.components.unused')}</option>
        </select>
        <label className="flex items-center gap-2 text-sm text-bambu-gray">
          <input type="checkbox" checked={showRetired} onChange={(e) => setShowRetired(e.target.checked)} />
          {t('farm.components.showRetired', { count: retiredCount })}
        </label>
      </div>
      <p className="text-xs text-bambu-gray">
        {t('farm.components.summary', { shapes: shapes.data?.length ?? 0, components: active.length })}
      </p>

      {loading ? (
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      ) : (
        <ul className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-start">
          {groups.map(({ shape, shown }) => {
            const count = shape ? plateCount.byShape.get(shape.id) || 0 : null;
            return (
              <li key={shape?.id ?? 'none'}
                className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg p-4 space-y-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <h3 className="text-white font-semibold flex items-center gap-2 min-w-0 flex-1">
                    <Layers className="w-4 h-4 text-bambu-green shrink-0" />
                    {shape ? (
                      <InlineName value={shape.name} label={`${t('farm.products.shape')} ${shape.name}`}
                        onSave={(name) => renameShape.mutate({ id: shape.id, name })} />
                    ) : (
                      t('farm.components.noShape')
                    )}
                  </h3>
                  {count !== null && (
                    <span className={`text-xs flex items-center gap-1 ${count ? 'text-bambu-gray' : 'text-amber-400'}`}>
                      {!count && <AlertTriangle className="w-3.5 h-3.5" />}
                      {count ? t('farm.products.plates', { count }) : t('farm.components.noPlateYet')}
                    </span>
                  )}
                </div>
                {shown.length > 0 && (
                  <ul className="divide-y divide-bambu-dark-tertiary">
                    {shown.map((c) => {
                      const used = usage.get(c.code) || [];
                      const loose = shape ? null : platesOf(c);
                      return (
                        <li key={c.code}
                          className={`py-2 flex flex-wrap items-start gap-x-3 gap-y-1 ${c.retired ? 'opacity-50' : ''}`}>
                          <Swatch color={c.color} />
                          <span className="font-mono text-bambu-green text-sm w-16 shrink-0">{c.code}</span>
                          <div className="min-w-0 flex-1 space-y-0.5">
                            <div className="text-sm text-white">
                              {shape ? (
                                c.color || '—'
                              ) : (
                                <InlineName value={c.name} label={`${t('farm.products.name')} ${c.code}`}
                                  onSave={(name) => updateComponent.mutate({ code: c.code, name })} />
                              )}
                            </div>
                            <p className="text-xs text-bambu-gray break-words">
                              {used.length
                                ? used.map((u) => `${u.quantity}× ${u.name}`).join(' · ')
                                : t('farm.components.notUsed')}
                              {loose !== null &&
                                ` · ${loose ? t('farm.products.plates', { count: loose }) : t('farm.components.noPlateYet')}`}
                              {c.retired && ` · ${t('farm.components.retired')}`}
                            </p>
                          </div>
                          <Button type="button" variant="ghost" size="sm" onClick={() => {
                            if (c.retired) updateComponent.mutate({ code: c.code, retired: false });
                            else if (window.confirm(t('farm.components.retireConfirm', { code: c.code, name: c.name })))
                              updateComponent.mutate({ code: c.code, retired: true });
                          }}>
                            {c.retired ? t('farm.products.restore') : t('farm.products.retire')}
                          </Button>
                        </li>
                      );
                    })}
                  </ul>
                )}
                {shape && (
                  <form className="flex gap-2" onSubmit={(e) => {
                    e.preventDefault();
                    const color = (newColor[shape.id] || '').trim();
                    if (color) createComponent.mutate({ shapeId: shape.id, color });
                  }}>
                    <input aria-label={`${t('farm.components.newColor')} ${shape.name}`}
                      className={`${INPUT} flex-1 py-1`} placeholder={t('farm.components.newColor')} maxLength={50}
                      value={newColor[shape.id] || ''}
                      onChange={(e) => setNewColor((prev) => ({ ...prev, [shape.id]: e.target.value }))} />
                    <Button type="submit" size="sm" variant="secondary" disabled={!(newColor[shape.id] || '').trim()}>
                      <Plus className="w-4 h-4" /> {t('farm.components.addColor')}
                    </Button>
                  </form>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <form className="flex gap-2 max-w-md" onSubmit={(e) => {
        e.preventDefault();
        if (newShape.trim()) createShape.mutate();
      }}>
        <input aria-label={t('farm.products.newShape')} className={`${INPUT} flex-1`} value={newShape} maxLength={200}
          placeholder={t('farm.products.newShape')} onChange={(e) => setNewShape(e.target.value)} />
        <Button type="submit" variant="secondary" disabled={!newShape.trim() || createShape.isPending}>
          <Plus className="w-4 h-4" /> {t('farm.products.createShape')}
        </Button>
      </form>
    </div>
  );
}
