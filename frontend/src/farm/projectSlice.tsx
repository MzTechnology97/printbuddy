// Fork (printfarm): slicing a library 3MF with its own project settings (the default in
// the SliceModal), and preset pre-picks that follow the 3MF when presets are chosen instead.
// Strings live here (it/en) instead of the upstream locale files, to keep merges small.
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { ProjectSettings } from './projectSliceApi';

const STRINGS = {
  it: {
    useProject: 'Impostazioni del progetto (3MF)',
    usePresets: 'Scegli i preset',
    projectHelp: 'Slicing con stampante, processo e filamenti salvati nel file, come quando lo apri nello slicer.',
    slicer: 'Slicer',
    printer: 'Stampante',
    process: 'Processo',
    filaments: 'Filamenti',
    plate: 'Piatto',
    plateDefault: 'quello predefinito del progetto',
    notSliceable: (slicer: string) =>
      `Questo file non si può slicare con le impostazioni del progetto (slicer: ${slicer}): scegli i preset.`,
  },
  en: {
    useProject: 'Project settings (3MF)',
    usePresets: 'Choose presets',
    projectHelp: 'Slice with the printer, process and filaments saved in the file, as when you open it in the slicer.',
    slicer: 'Slicer',
    printer: 'Printer',
    process: 'Process',
    filaments: 'Filaments',
    plate: 'Plate',
    plateDefault: "the project's default",
    notSliceable: (slicer: string) =>
      `This file can't be sliced with its project settings (slicer: ${slicer}): choose presets.`,
  },
};

const SLICER_NAMES: Record<string, string> = { orcaslicer: 'OrcaSlicer', bambu_studio: 'Bambu Studio' };

function useStrings() {
  const { i18n } = useTranslation();
  return i18n.language?.startsWith('it') ? STRINGS.it : STRINGS.en;
}

export function ProjectModeToggle({
  projectMode,
  onChange,
  disabled,
}: {
  projectMode: boolean;
  onChange: (projectMode: boolean) => void;
  disabled?: boolean;
}) {
  const s = useStrings();
  const option = (value: boolean, label: string) => (
    <label className="flex items-center gap-2 text-sm text-white cursor-pointer">
      <input
        type="radio"
        name="farm-slice-mode"
        checked={projectMode === value}
        onChange={() => onChange(value)}
        disabled={disabled}
      />
      {label}
    </label>
  );
  return (
    <div className="flex flex-wrap gap-4" role="radiogroup">
      {option(true, s.useProject)}
      {option(false, s.usePresets)}
    </div>
  );
}

export function ProjectSettingsPanel({ info, plate }: { info: ProjectSettings; plate: number | null }) {
  const s = useStrings();
  if (!info.sliceable) {
    return <p className="text-sm text-amber-400">{s.notSliceable(SLICER_NAMES[info.slicer] ?? info.slicer)}</p>;
  }
  const row = (label: string, value: ReactNode) => (
    <div className="flex gap-3 text-sm">
      <span className="w-24 flex-shrink-0 text-bambu-gray">{label}</span>
      <span className="text-white min-w-0 break-words">{value}</span>
    </div>
  );
  const printer = [info.printer_preset ?? info.printer_model_raw, info.printer_model && `(${info.printer_model})`]
    .filter(Boolean)
    .join(' ');
  return (
    <div className="space-y-2 rounded-md border border-bambu-dark-tertiary bg-bambu-dark/40 p-3">
      <p className="text-xs text-bambu-gray">{s.projectHelp}</p>
      {row(s.slicer, `${SLICER_NAMES[info.slicer] ?? info.slicer}${info.sidecar_url ? ` · ${info.sidecar_url}` : ''}`)}
      {row(s.printer, printer || '—')}
      {row(s.process, info.process_preset ?? '—')}
      {row(
        s.filaments,
        info.filaments.length === 0 ? (
          '—'
        ) : (
          <ul className="space-y-1">
            {info.filaments.map((f) => (
              <li key={f.index} className="flex items-center gap-2">
                <span
                  className="inline-block w-3 h-3 rounded-full border border-white/30 flex-shrink-0"
                  style={{ backgroundColor: f.color ?? 'transparent' }}
                  aria-hidden
                />
                <span>
                  {f.index}. {f.preset ?? f.type ?? '—'}
                  {f.preset && f.type ? ` (${f.type})` : ''}
                </span>
              </li>
            ))}
          </ul>
        ),
      )}
      {row(s.plate, plate != null ? String(plate) : s.plateDefault)}
    </div>
  );
}
