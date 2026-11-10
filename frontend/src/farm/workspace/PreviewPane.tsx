// Fork (printfarm): the Preview tab: the plate's sliced G-code as OrcaSlicer's preview shows it (legend with its
// view types and statistics, layer and move sliders; see gcode/GcodePreview.tsx), or the button to slice it.
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Cog } from 'lucide-react';
import { api } from '../../api/client';
import { Button } from '../../components/Button';
import { GcodePreview } from './gcode/GcodePreview';
import { type OrcaLayout, orcaText } from './orcaLayout';
import '../i18n';

interface Props {
  plate: number | null;
  /** The plate's slice to show: none yet, or one of another version of the 3MF (``current`` false). */
  output: { library_file_id: number; current: boolean } | null;
  bed?: { x: number; y: number; z: number };
  colours: string[];
  layout: OrcaLayout;
  canSlice: boolean;
  onSlice: () => void;
}

export function PreviewPane({ plate, output, bed, colours, layout, canSlice, onSlice }: Props) {
  const { t, i18n } = useTranslation();
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
  return (
    <GcodePreview
      key={output.library_file_id}
      url={api.getLibraryFileGcodeUrl(output.library_file_id)}
      bed={bed ?? { x: 256, y: 256, z: 256 }}
      colours={colours}
      o={(text) => orcaText(layout, text, i18n.language)}
      phaseTitle={(phase) => t('farm.workspace.previewPane.phase', { phase })}
      labels={{
        loading: t('farm.workspace.previewPane.loading'),
        failed: t('farm.workspace.previewPane.failed'),
        oneLayer: t('farm.workspace.previewPane.oneLayer'),
        tooLarge: t('farm.workspace.previewPane.tooLarge'),
        memory: t('farm.workspace.previewPane.memory'),
        upperLayer: t('farm.workspace.previewPane.upperLayer'),
        lowerLayer: t('farm.workspace.previewPane.lowerLayer'),
        move: t('farm.workspace.previewPane.move'),
        layerText: (layer, z) => t('farm.workspace.previewPane.layerText', { layer, z }),
      }}
      notice={
        !output.current && (
          <p className="absolute top-2 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1 rounded bg-bambu-dark-secondary/90 px-2 py-1 text-xs text-amber-400">
            <AlertTriangle className="w-3.5 h-3.5" /> {t('farm.workspace.previewPane.stale')}
          </p>
        )
      }
    />
  );
}
