// Printfarm (fork): the distinct pieces on all plates, recognised by geometry. Link a piece to its shape once
// and every plate printing it follows; plates whose objects all have a shape are confirmed.
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, CheckCircle2, Loader2, Search } from 'lucide-react';
import { api } from '../api/client';
import { Button } from '../components/Button';
import { useToast } from '../contexts/ToastContext';
import { farmApi, type FarmPiece, type FarmShape } from './api';
import './i18n';

const INPUT =
  'bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';
const PAGE = 40;
const NEW_SHAPE = '__new__';

function PieceCard({ piece, shapes }: { piece: FarmPiece; shapes: FarmShape[] }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [choice, setChoice] = useState(String(piece.shape_id ?? piece.suggested_shape_id ?? ''));
  const [newName, setNewName] = useState(piece.names[0]?.replace(/\.(stl|3mf|step|obj)$/i, '') ?? '');
  const [thumbFailed, setThumbFailed] = useState(false);

  const link = useMutation({
    mutationFn: async () => {
      const shapeId = choice === NEW_SHAPE ? (await farmApi.createShape(newName.trim())).id : Number(choice);
      return farmApi.linkPiece(piece.yield_ids, shapeId);
    },
    onSuccess: (r) => {
      queryClient.invalidateQueries({ queryKey: ['farm'] });
      showToast(t('farm.pieces.linked', { objects: r.linked, plates: r.plates_confirmed }), 'success');
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const suggested = !piece.shape_id && !!piece.suggested_shape_id && choice === String(piece.suggested_shape_id);
  const ready = choice === NEW_SHAPE ? !!newName.trim() : !!choice;
  return (
    <li className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg p-3 flex flex-col sm:flex-row gap-3">
      <div className="w-full sm:w-28 h-28 rounded bg-bambu-dark shrink-0 flex items-center justify-center text-xs text-bambu-gray">
        {thumbFailed ? (
          t('farm.plates.plate', { index: piece.sample.plate_index })
        ) : (
          <img src={api.getLibraryFilePlateThumbnail(piece.sample.library_file_id, piece.sample.plate_index)}
            alt="" loading="lazy" className="w-full h-full object-contain rounded" onError={() => setThumbFailed(true)} />
        )}
      </div>
      <div className="flex-1 min-w-0 space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <p className="text-sm text-white break-words min-w-0 flex-1">{piece.names.join(' · ')}</p>
          {piece.unlinked ? (
            <span className="text-xs text-amber-400">{t('farm.pieces.toLink', { count: piece.unlinked })}</span>
          ) : (
            <span className="text-xs text-bambu-green flex items-center gap-1">
              <CheckCircle2 className="w-3.5 h-3.5" /> {t('farm.pieces.isLinked')}
            </span>
          )}
        </div>
        <p className="text-xs text-bambu-gray break-words">
          {t('farm.pieces.where', { objects: piece.objects, plates: piece.plates, files: piece.file_count })}
          {' · '}
          {piece.files.join(', ')}
          {piece.file_count > piece.files.length && ', …'}
        </p>
        {piece.mixed && (
          <p className="text-xs text-amber-400 flex items-center gap-1">
            <AlertTriangle className="w-3.5 h-3.5" /> {t('farm.pieces.mixed')}
          </p>
        )}
        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => {
          e.preventDefault();
          if (ready) link.mutate();
        }}>
          <select aria-label={`${t('farm.products.shape')} ${piece.names[0] ?? ''}`}
            className={`${INPUT} py-1 w-full sm:w-64 ${suggested ? 'border-bambu-green/60' : ''}`}
            value={choice} onChange={(e) => setChoice(e.target.value)}>
            <option value="">{t('farm.pieces.pickShape')}</option>
            {shapes.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            <option value={NEW_SHAPE}>{t('farm.plates.newShapeOption')}</option>
          </select>
          {choice === NEW_SHAPE && (
            <input aria-label={t('farm.products.newShape')} className={`${INPUT} py-1 flex-1 min-w-[12rem]`}
              maxLength={200} value={newName} onChange={(e) => setNewName(e.target.value)} />
          )}
          {suggested && <span className="text-xs text-bambu-green">{t('farm.plates.suggested')}</span>}
          <Button type="submit" size="sm" variant={piece.unlinked ? 'primary' : 'secondary'}
            disabled={!ready || link.isPending}>
            {link.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : t('farm.pieces.link')}
          </Button>
        </form>
      </div>
    </li>
  );
}

export function PiecesPanel() {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [all, setAll] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const pieces = useQuery({ queryKey: ['farm', 'pieces'], queryFn: farmApi.getPieces });
  const shapes = useQuery({ queryKey: ['farm', 'shapes'], queryFn: farmApi.getShapes });

  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const list = pieces.data || [];
  const todo = list.filter((p) => p.unlinked > 0).length;
  const shown = list
    .filter((p) => all || p.unlinked > 0)
    .filter((p) => {
      const text = [...p.names, ...p.files].join(' ').toLowerCase();
      return words.every((w) => text.includes(w));
    });

  return (
    <div className="space-y-4">
      <p className="text-bambu-gray text-sm max-w-3xl">{t('farm.pieces.intro')}</p>
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[16rem] max-w-md">
          <Search className="w-4 h-4 text-bambu-gray absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input type="search" aria-label={t('farm.pieces.search')} placeholder={t('farm.pieces.search')}
            className={`${INPUT} w-full pl-8`} value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <label className="flex items-center gap-2 text-sm text-bambu-gray">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
          {t('farm.pieces.showLinked')}
        </label>
      </div>
      <p className="text-xs text-bambu-gray">{t('farm.pieces.summary', { pieces: list.length, todo })}</p>
      {pieces.isLoading ? (
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      ) : pieces.error ? (
        <p className="text-sm text-red-400">{(pieces.error as Error).message}</p>
      ) : shown.length === 0 ? (
        <p className="text-sm text-bambu-gray">{t('farm.pieces.none')}</p>
      ) : (
        <>
          <ul className="space-y-2">
            {shown.slice(0, limit).map((p) => (
              <PieceCard key={`${p.yield_ids[0]}-${p.shape_id}-${p.unlinked}`} piece={p} shapes={shapes.data || []} />
            ))}
          </ul>
          {shown.length > limit && (
            <Button type="button" variant="secondary" onClick={() => setLimit(limit + PAGE)}>
              {t('farm.plates.more', { count: shown.length - limit })}
            </Button>
          )}
        </>
      )}
    </div>
  );
}
