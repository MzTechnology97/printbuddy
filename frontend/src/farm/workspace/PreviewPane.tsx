// Fork (printfarm): the Preview tab: the plate's sliced G-code in the existing viewer, in the slicer layout
// (vertical layer slider, move slider, legend by feature type), and time and filament of the slice.
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Cog } from 'lucide-react';
import { api } from '../../api/client';
import { Button } from '../../components/Button';
import { GcodeViewer } from '../../components/GcodeViewer';
import { formatDuration } from '../../utils/date';
import { FEATURE_GROUPS } from '../../utils/gcodeFeatures';
import '../i18n';

interface Props {
  plate: number | null;
  /** The plate's slice to show: none yet, or one of another version of the 3MF (``current`` false). */
  output: { library_file_id: number; current: boolean } | null;
  bed?: { x: number; y: number; z: number };
  colours: string[];
  canSlice: boolean;
  onSlice: () => void;
}

export function PreviewPane({ plate, output, bed, colours, canSlice, onSlice }: Props) {
  const { t } = useTranslation();
  const stats = useQuery({
    queryKey: ['libraryFilePlates', output?.library_file_id],
    queryFn: () => api.getLibraryFilePlates(output!.library_file_id),
    enabled: output != null,
  });
  if (!output) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3 text-center p-4">
        <p className="text-bambu-gray">{t('farm.workspace.previewPane.none', { index: plate ?? 1 })}</p>
        <Button size="sm" onClick={onSlice} disabled={!canSlice}>
          <Cog className="w-4 h-4" />
          {t('farm.workspace.slicePlate')}
        </Button>
      </div>
    );
  }
  const info = stats.data?.plates?.find((p) => p.index === plate) ?? stats.data?.plates?.[0];
  const labels = FEATURE_GROUPS.map((g) => t(`farm.workspace.features.${g}`));
  const grams = info?.filaments?.reduce((sum, f) => sum + (f.used_grams || 0), 0) ?? 0;
  return (
    <div className="relative h-full">
      <GcodeViewer
        key={output.library_file_id}
        gcodeUrl={api.getLibraryFileGcodeUrl(output.library_file_id)}
        buildVolume={bed}
        filamentColors={colours}
        colorBy="feature"
        controls="slicer"
        featureLabels={labels}
        className="w-full h-full"
      />
      <div className="absolute bottom-28 left-2 right-12 sm:bottom-auto sm:left-auto sm:top-2 sm:w-56 rounded-md bg-bambu-dark-secondary/90 p-2 text-xs text-white space-y-1">
        {!output.current && (
          <p className="flex items-center gap-1 text-amber-400">
            <AlertTriangle className="w-3.5 h-3.5" /> {t('farm.workspace.previewPane.stale')}
          </p>
        )}
        {info?.print_time_seconds != null && (
          <div className="flex justify-between">
            <span className="text-bambu-gray">{t('farm.workspace.previewPane.time')}</span>
            <span>{formatDuration(info.print_time_seconds)}</span>
          </div>
        )}
        {(info?.filaments ?? []).map((f) => (
          <div key={f.slot_id} className="flex items-center gap-2">
            <span className="inline-block w-3 h-3 rounded-sm border border-white/30" style={{ backgroundColor: f.color }} />
            <span className="flex-1 truncate">{f.type}</span>
            <span className="text-bambu-gray">{f.used_meters ? `${f.used_meters.toFixed(2)} m` : ''}</span>
            <span>{f.used_grams.toFixed(1)} g</span>
          </div>
        ))}
        {(info?.filaments?.length ?? 0) > 1 && (
          <div className="flex justify-between border-t border-bambu-dark-tertiary pt-1">
            <span className="text-bambu-gray">{t('farm.workspace.previewPane.total')}</span>
            <span>{grams.toFixed(1)} g</span>
          </div>
        )}
      </div>
    </div>
  );
}
