// Printfarm (fork): the plates of the NAS 3MF files. Their pieces are linked on each product's sheet:
// the same 3MF can serve several products, each reading it with its own shapes.
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, FileBox, Loader2, Search } from 'lucide-react';
import { api } from '../api/client';
import { Button } from '../components/Button';
import { ModelViewerModal } from '../components/ModelViewerModal';
import { farmApi, type FarmLibraryFileState, type FarmPlateDetail } from './api';
import './i18n';

const INPUT =
  'bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green';
const PAGE = 40;
// Same rule as the NAS analysis: old, test, archived, sliced and copied files are not production files.
const NOT_PRODUCTION = /(^|[\\/ _-])(old|test\w*|archiviat\w*|gcode|copia)([\\/ _.-]|$)/i;

const duration = (s: number | null) => (s ? `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m` : null);

/** The plate's preview from the 3MF (``Metadata/plate_N.png``); a click opens the 3MF in the 3D viewer. */
export function PlateThumb({ fileId, plateIndex, fileName, className = 'w-full sm:w-32 h-32' }: {
  fileId: number;
  plateIndex: number;
  fileName: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        title={t('farm.plates.open3d')}
        aria-label={`${t('farm.plates.open3d')} ${t('farm.plates.plate', { index: plateIndex })}`}
        onClick={() => setOpen(true)}
        className={`${className} rounded bg-bambu-dark-secondary shrink-0 flex items-center justify-center text-sm text-bambu-gray hover:ring-2 hover:ring-bambu-green`}
      >
        {failed ? (
          t('farm.plates.view3d')
        ) : (
          <img
            src={api.getLibraryFilePlateThumbnail(fileId, plateIndex)}
            alt=""
            loading="lazy"
            className="w-full h-full object-contain rounded"
            onError={() => setFailed(true)}
          />
        )}
      </button>
      {open && (
        <ModelViewerModal libraryFileId={fileId} title={fileName} fileType="3mf" onClose={() => setOpen(false)} />
      )}
    </>
  );
}

function PlateCard({ plate, fileName }: { plate: FarmPlateDetail; fileName: string }) {
  const { t } = useTranslation();
  const facts = [
    plate.target_model || plate.printer_model,
    (plate.materials || []).join('+'),
    plate.nozzle_diameter ? `${plate.nozzle_diameter} mm` : null,
    duration(plate.print_time_seconds),
    plate.filament_grams ? `${Math.round(plate.filament_grams)} g` : null,
  ].filter(Boolean);

  return (
    <li className="bg-bambu-dark rounded-lg p-3 flex flex-col sm:flex-row gap-3">
      <PlateThumb fileId={plate.library_file_id} plateIndex={plate.plate_index} fileName={fileName} />
      <div className="flex-1 min-w-0 space-y-2">
        <p className="text-sm text-white">
          {t('farm.plates.plate', { index: plate.plate_index })}
          {plate.name && <span className="text-bambu-gray"> · {plate.name}</span>}
          <span className="text-xs text-bambu-gray"> · {facts.join(' · ')}</span>
        </p>
        {plate.warning && <p className="text-xs text-amber-400">{plate.warning}</p>}
        <ul className="space-y-1">
          {plate.yields.map((y) => (
            <li key={y.object_name} className="flex items-center gap-2 text-sm">
              <span className="text-white w-10 text-right shrink-0">{y.quantity}×</span>
              <span className="text-bambu-gray break-all min-w-0 flex-1">{y.object_name}</span>
            </li>
          ))}
        </ul>
      </div>
    </li>
  );
}

/** ``aside``: plates of the file shown last, folded (e.g. only pieces that are not part of a product). */
export function FilePlates({ file, aside = [] }: {
  file: Pick<FarmLibraryFileState, 'library_file_id' | 'path'>;
  aside?: number[];
}) {
  const { t } = useTranslation();
  const plates = useQuery({
    queryKey: ['farm', 'file-plates', file.library_file_id],
    queryFn: () => farmApi.getFilePlates(file.library_file_id!),
  });
  if (plates.isLoading) return <Loader2 className="w-5 h-5 animate-spin text-bambu-green m-3" />;
  if (plates.error) return <p className="text-sm text-red-400 p-3">{(plates.error as Error).message}</p>;
  if (!plates.data?.length) return <p className="text-sm text-bambu-gray p-3">{t('farm.plates.none')}</p>;
  const card = (p: FarmPlateDetail) => (
    <PlateCard key={p.id} plate={p} fileName={file.path.split(/[\\/]/).pop() || file.path} />
  );
  const later = plates.data.filter((p) => aside.includes(p.id));
  return (
    <ul className="space-y-2 p-3 pt-0">
      {plates.data.filter((p) => !aside.includes(p.id)).map(card)}
      {later.length > 0 && (
        <li>
          <details>
            <summary className="text-sm text-bambu-gray cursor-pointer">
              {t('farm.project.unrelatedPlates', { count: later.length })}
            </summary>
            <ul className="space-y-2 mt-2">{later.map(card)}</ul>
          </details>
        </li>
      )}
    </ul>
  );
}

export function PlatesPanel() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const [query, setQuery] = useState(params.get('q') || ''); // ?q= from a product's page
  const [allFiles, setAllFiles] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const [limit, setLimit] = useState(PAGE);

  const files = useQuery({ queryKey: ['farm', 'library-files'], queryFn: farmApi.getFarmFiles });
  const summary = useQuery({ queryKey: ['farm', 'plates-summary'], queryFn: farmApi.getPlatesSummary });
  const counts = useMemo(() => new Map((summary.data || []).map((s) => [s.library_file_id, s])), [summary.data]);

  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const shown = (files.data || []).filter((f) => {
    if (f.library_file_id == null || !f.path.toLowerCase().endsWith('.3mf')) return false;
    if (!allFiles && (!['orcaslicer', 'bambu_studio'].includes(f.slicer || '') || NOT_PRODUCTION.test(f.path)))
      return false;
    const text = f.path.toLowerCase();
    return words.every((w) => text.includes(w));
  });

  return (
    <div className="space-y-4">
      <p className="text-bambu-gray text-sm max-w-3xl">{t('farm.plates.intro')}</p>
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[16rem] max-w-md">
          <Search className="w-4 h-4 text-bambu-gray absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input type="search" aria-label={t('farm.plates.search')} placeholder={t('farm.plates.search')}
            className={`${INPUT} w-full pl-8`} value={query}
            onChange={(e) => { setQuery(e.target.value); setLimit(PAGE); }} />
        </div>
        <label className="flex items-center gap-2 text-sm text-bambu-gray">
          <input type="checkbox" checked={allFiles} onChange={(e) => setAllFiles(e.target.checked)} />
          {t('farm.plates.allFiles')}
        </label>
      </div>
      <p className="text-xs text-bambu-gray">
        {t('farm.plates.files', { count: shown.length })}
      </p>

      {files.isLoading ? (
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      ) : (
        <ul className="space-y-2">
          {shown.slice(0, limit).map((f) => {
            const c = counts.get(f.library_file_id!);
            const isOpen = open === f.library_file_id;
            const slash = Math.max(f.path.lastIndexOf('/'), f.path.lastIndexOf('\\'));
            return (
              <li key={f.library_file_id}
                className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
                <button type="button" aria-expanded={isOpen}
                  onClick={() => setOpen(isOpen ? null : f.library_file_id)}
                  className="w-full text-left p-3 flex items-start gap-2">
                  {isOpen ? (
                    <ChevronDown className="w-4 h-4 mt-0.5 shrink-0" />
                  ) : (
                    <ChevronRight className="w-4 h-4 mt-0.5 shrink-0" />
                  )}
                  <FileBox className="w-4 h-4 mt-0.5 text-bambu-gray shrink-0" />
                  <span className="min-w-0 flex-1 break-all">
                    <span className="text-xs text-bambu-gray block">{f.path.slice(0, slash + 1)}</span>
                    <span className="text-sm text-white">{f.path.slice(slash + 1)}</span>
                  </span>
                  <span className="text-xs text-bambu-gray shrink-0 text-right">
                    {f.printer_model}
                    <br />
                    {c ? t('farm.plates.plateCount', { count: c.proposed + c.confirmed }) : t('farm.plates.notOpened')}
                  </span>
                </button>
                {isOpen && <FilePlates file={f} />}
              </li>
            );
          })}
        </ul>
      )}
      {shown.length > limit && (
        <Button variant="secondary" onClick={() => setLimit(limit + PAGE)}>
          {t('farm.plates.more', { count: shown.length - limit })}
        </Button>
      )}
    </div>
  );
}
