// Printfarm (fork): the pieces of a product, from the 3MF of its folders (recognised by geometry, #33).
// One save per piece: "how many per product" sets the shape everywhere, the component and the part.
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Boxes, CheckCircle2, Loader2 } from 'lucide-react';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type FarmProductPiece, type FarmProductPieces, type FarmShape } from './api';
import './i18n';

const INPUT =
  'bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1 text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';
const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const cleanName = (s: string) => s.replace(/\.(stl|3mf|step|stp|obj)$/i, '').replace(/\s*\(\d+\)\s*$/g, '').trim();

function PieceRow({ projectId, piece, data, shapes, colors }: {
  projectId: number;
  piece: FarmProductPiece;
  data: FarmProductPieces;
  shapes: FarmShape[];
  colors: Record<string, string>;
}) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const suggested = shapes.find((s) => s.id === piece.suggested_shape_id);
  const [name, setName] = useState(piece.shape || suggested?.name || cleanName(piece.names[0] || ''));
  const [color, setColor] = useState(piece.component?.color || data.default_color || '');
  const [component, setComponent] = useState('');
  const [quantity, setQuantity] = useState(piece.quantity != null ? String(piece.quantity) : '');
  const [imageFailed, setImageFailed] = useState(false);
  const isSet = data.kind === 'set';
  const linked = piece.component != null || (isSet && data.set != null);
  const qtyLabel = t(isSet ? 'farm.productPieces.perSetInput' : 'farm.productPieces.perProduct');
  const hex = piece.component?.color_hex || data.set?.color_hex || colors[color.toLowerCase()] || null;

  const sameAs = useMutation({
    mutationFn: async (into: number) => {
      if (piece.shape_id) await farmApi.mergeShape(piece.shape_id, into);
      else {
        // Its geometry joins that shape of this product (never another product's).
        const quantity = data.pieces.find((p) => p.shape_id === into)?.quantity ?? 1;
        await farmApi.setProductPiece(projectId, { yield_ids: piece.yield_ids, quantity, shape_id: into });
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['farm'] }),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  // The product's other shapes: a version of the model in another 3MF can be told to be one of them.
  const others = [
    ...new Map(
      data.pieces.filter((p) => p.shape_id && p.shape_id !== piece.shape_id).map((p) => [p.shape_id!, p.shape!]),
    ),
  ];
  const notPart = useMutation({
    mutationFn: () => farmApi.setProductPiece(projectId, { yield_ids: piece.yield_ids, quantity: 0 }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['farm'] }),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const save = useMutation({
    mutationFn: () =>
      farmApi.setProductPiece(projectId, {
        yield_ids: piece.yield_ids,
        quantity: Number(quantity) || 0,
        ...(piece.shape_id
          ? {}
          : suggested && name.trim() === suggested.name
            ? { shape_id: suggested.id }
            : { shape_name: name.trim() }),
        ...(component ? { component_code: component } : {}),
        ...(color ? { color } : {}),
      }),
    onSuccess: (r) => {
      queryClient.invalidateQueries({ queryKey: ['farm'] });
      showToast(r.component_code ? t('farm.productPieces.saved', { code: r.component_code }) : t('farm.productPieces.removed'), 'success');
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const label = piece.shape || name || piece.names[0];
  return (
    <li className="bg-bambu-dark rounded-lg p-3 flex flex-col sm:flex-row gap-3">
      {/* Light tile: a black piece must stay visible on the dark page. */}
      <div className="w-full sm:w-28 h-28 shrink-0 rounded bg-gray-300 flex items-center justify-center overflow-hidden">
        {imageFailed ? (
          <Boxes className="w-8 h-8 text-bambu-gray/50" />
        ) : (
          <img src={farmApi.pieceImageUrl(piece.image_yield_id, hex)} alt={label} loading="lazy"
            className="w-full h-full object-contain" onError={() => setImageFailed(true)} />
        )}
      </div>
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          {piece.shape ? (
            <span className="text-white">{piece.shape}</span>
          ) : (
            <input aria-label={`${t('farm.productPieces.name')} ${piece.names[0]}`} className={`${INPUT} flex-1 min-w-40`}
              value={name} placeholder={t('farm.productPieces.name')} onChange={(e) => setName(e.target.value)} />
          )}
          {piece.hand !== 0 && <span className="text-xs text-bambu-gray">· {t('farm.productPieces.mirrored')}</span>}
          {others.length > 0 && (
            <select aria-label={`${t('farm.productPieces.sameAs')} ${piece.shape || piece.names[0]}`} className={INPUT}
              value="" disabled={sameAs.isPending}
              onChange={(e) => {
                const into = Number(e.target.value);
                const shape = others.find(([id]) => id === into)?.[1];
                if (into && window.confirm(t('farm.productPieces.sameAsConfirm', { piece: piece.shape || piece.names[0], shape }))) {
                  sameAs.mutate(into);
                }
              }}>
              <option value="">{t('farm.productPieces.sameAs')}</option>
              {others.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
          )}
          {piece.quantity == null ? (
            <span className="text-xs text-amber-400">{t('farm.productPieces.open')}</span>
          ) : (
            <CheckCircle2 className="w-4 h-4 text-bambu-green" aria-hidden />
          )}
        </div>
        <p className="text-xs text-bambu-gray break-words">
          {t('farm.productPieces.where', { plates: piece.plates, models: piece.printer_models.join(', ') || '—' })}
          {' · '}
          {piece.files.join(', ')}
          {piece.file_count > piece.files.length && ` (+${piece.file_count - piece.files.length})`}
          {piece.names.length > 1 && ` · ${piece.names.join(', ')}`}
        </p>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {linked ? (
            <span className="text-bambu-gray flex items-center gap-1">
              {hex && <span aria-hidden className="w-3 h-3 rounded-full border border-bambu-gray/60" style={{ background: `#${hex}` }} />}
              <span className="font-mono text-bambu-green">{(piece.component ?? data.set)!.code}</span>{' '}
              {(piece.component ?? data.set)!.name}
            </span>
          ) : (
            <>
              <select aria-label={`${t('farm.productPieces.color')} ${label}`} className={INPUT} value={color}
                onChange={(e) => setColor(e.target.value)}>
                <option value="">{t('farm.productPieces.noColor')}</option>
                {Object.keys(colors).map((c) => (
                  <option key={c} value={capitalize(c)}>{capitalize(c)}</option>
                ))}
              </select>
              {!isSet && data.unshaped_parts.length > 0 && (
                <select aria-label={`${t('farm.productPieces.component')} ${label}`} className={`${INPUT} max-w-full`}
                  value={component} onChange={(e) => setComponent(e.target.value)}>
                  <option value="">{t('farm.productPieces.newComponent')}</option>
                  {data.unshaped_parts.map((c) => (
                    <option key={c.code} value={c.code}>{c.code} {c.name}</option>
                  ))}
                </select>
              )}
            </>
          )}
          <label className="flex items-center gap-1 text-bambu-gray">
            {qtyLabel}
            <input type="number" min={0} max={1000} aria-label={`${qtyLabel} ${label}`}
              className={`${INPUT} w-20`} value={quantity} onChange={(e) => setQuantity(e.target.value)} />
          </label>
          <Button size="sm" disabled={quantity === '' || (!piece.shape_id && !name.trim()) || save.isPending}
            onClick={() => save.mutate()}>
            {save.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : t('farm.productPieces.save')}
          </Button>
          {piece.quantity == null && (
            <Button size="sm" variant="secondary" disabled={notPart.isPending} title={t('farm.productPieces.notPartHint')}
              aria-label={`${t('farm.productPieces.notPart')} ${label}`} onClick={() => notPart.mutate()}>
              {t('farm.productPieces.notPart')}
            </Button>
          )}
        </div>
      </div>
    </li>
  );
}

/** A piece set aside as not part of the product: its picture, its name and "Put back". */
function AsideRow({ projectId, piece }: { projectId: number; piece: FarmProductPiece }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const restore = useMutation({
    mutationFn: () => farmApi.restoreProductPiece(projectId, piece.yield_ids),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['farm'] }),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const label = piece.shape || piece.names[0];
  return (
    <li className="flex items-center gap-3 text-sm">
      <img src={farmApi.pieceImageUrl(piece.image_yield_id, null)} alt="" loading="lazy"
        className="w-12 h-12 rounded bg-gray-300 object-contain shrink-0" />
      <span className="min-w-0 flex-1 break-all text-bambu-gray">{label} · {piece.files.join(', ')}</span>
      <Button size="sm" variant="secondary" disabled={restore.isPending}
        aria-label={`${t('farm.productPieces.restore')} ${label}`} onClick={() => restore.mutate()}>
        {t('farm.productPieces.restore')}
      </Button>
    </li>
  );
}

export function ProductPiecesPanel({ projectId }: { projectId: number }) {
  const { t } = useTranslation();
  const pieces = useQuery({ queryKey: ['farm', 'product-pieces', projectId], queryFn: () => farmApi.getProductPieces(projectId) });
  const shapes = useQuery({ queryKey: ['farm', 'shapes', projectId], queryFn: () => farmApi.getShapes(projectId) });
  const settings = useQuery({ queryKey: ['farm', 'plan-settings'], queryFn: farmApi.getPlanSettings });
  const data = pieces.data;
  const main = data?.pieces.filter((p) => !p.not_part) ?? [];
  const aside = data?.pieces.filter((p) => p.not_part) ?? [];
  const open = main.filter((p) => p.quantity == null).length;
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-white">{t('farm.productPieces.title')}</h3>
      <p className="text-xs text-bambu-gray max-w-3xl">{t('farm.productPieces.intro')}</p>
      {pieces.isLoading ? (
        <Loader2 className="w-5 h-5 animate-spin text-bambu-green" />
      ) : !data?.pieces.length ? (
        <p className="text-sm text-bambu-gray">{t('farm.productPieces.none')}</p>
      ) : (
        <>
          <p className="text-xs text-bambu-gray">
            {t('farm.productPieces.counts', { open, done: main.length - open })}
          </p>
          <ul className="space-y-2">
            {main.map((p) => (
              <PieceRow key={`${p.yield_ids[0]}-${p.quantity}-${p.component?.code}`} projectId={projectId} piece={p}
                data={data} shapes={shapes.data || []} colors={settings.data?.colors || {}} />
            ))}
          </ul>
          {aside.length > 0 && (
            <details className="bg-bambu-dark rounded-lg p-3">
              <summary className="text-sm text-bambu-gray cursor-pointer">
                {t('farm.productPieces.aside', { count: aside.length })}
              </summary>
              <ul className="space-y-2 mt-2">
                {aside.map((p) => <AsideRow key={p.yield_ids[0]} projectId={projectId} piece={p} />)}
              </ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
