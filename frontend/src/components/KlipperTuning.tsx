import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import type { PrinterStatus } from '../api/client';
import { ConfirmModal } from './ConfirmModal';

type ToastType = 'success' | 'error' | 'warning' | 'info' | 'loading';

interface Props {
  printerId: number;
  status?: Partial<Pick<PrinterStatus, 'klipper_tuning' | 'state' | 'connected'>>;
  showToast: (msg: string, type?: ToastType) => void;
}

const btn =
  'px-2 py-1 rounded text-xs border border-[var(--accent)] bg-[var(--bg-primary)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed';
const input =
  'w-20 rounded px-2 py-1 text-sm bg-[var(--bg-primary)] border border-[var(--border-color)] focus:outline-none focus:border-[var(--accent)]';

/** Height (mm) -> colour: blue below the mean plane, red above, white at 0; scaled on the largest deviation. */
function meshColour(value: number, scale: number): string {
  const t = scale > 0 ? Math.max(-1, Math.min(1, value / scale)) : 0;
  const light = Math.round(255 * (1 - Math.abs(t)));
  return t >= 0 ? `rgb(255,${light},${light})` : `rgb(${light},${light},255)`;
}

/** The active bed mesh as a heat map (front of the bed at the bottom), its range, and a new calibration. */
function BedMesh({ printerId, printing, showToast }: { printerId: number; printing: boolean; showToast: Props['showToast'] }) {
  const { t } = useTranslation();
  const [askCalibrate, setAskCalibrate] = useState(false);
  const [open, setOpen] = useState(false);
  // Read on demand: a probed matrix can be 50x50 points.
  const mesh = useQuery({ queryKey: ['klipperBedMesh', printerId], queryFn: () => api.klipperBedMesh(printerId), enabled: open });
  const calibrate = useMutation({
    mutationFn: () => api.klipperCalibrateBedMesh(printerId),
    onSuccess: () => showToast(t('printers.klipper.meshCalibrating', 'Calibration started: reload the mesh when it ends'), 'info'),
    onError: (error: Error) => showToast(error.message, 'error'),
  });
  const rows = mesh.data?.probed_matrix ?? [];
  const values = rows.flat();
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 0;
  const scale = Math.max(Math.abs(min), Math.abs(max));

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 flex-wrap text-xs text-[var(--text-muted)]">
        <span className="font-medium text-[var(--text-secondary)]">{t('printers.klipper.bedMesh', 'Bed mesh')}</span>
        {mesh.data?.profile_name && <span>{mesh.data.profile_name}</span>}
        {values.length > 0 && (
          <span className="font-mono tabular-nums">
            {t('printers.klipper.meshRange', {
              range: (max - min).toFixed(3),
              min: min.toFixed(3),
              max: max.toFixed(3),
              defaultValue: 'range {{range}} mm ({{min}} / {{max}})',
            })}
          </span>
        )}
        <button className={btn} onClick={() => (open ? mesh.refetch() : setOpen(true))} disabled={mesh.isFetching}>
          {open ? t('printers.klipper.meshReload', 'Reload') : t('printers.klipper.meshShow', 'Show')}
        </button>
        <button className={btn} onClick={() => setAskCalibrate(true)} disabled={printing || calibrate.isPending}>
          {t('printers.klipper.meshCalibrate', 'Calibrate')}
        </button>
      </div>
      {mesh.isError && <span className="text-xs text-red-400">{t('printers.klipper.meshUnavailable', 'Bed mesh unavailable')}</span>}
      {mesh.isSuccess && rows.length === 0 && (
        <span className="text-xs text-[var(--text-muted)]">{t('printers.klipper.meshNone', 'No mesh loaded')}</span>
      )}
      {rows.length > 0 && (
        <div
          role="img"
          aria-label={t('printers.klipper.bedMesh', 'Bed mesh')}
          className="grid w-full max-w-[16rem] aspect-square border border-[var(--border-color)]"
          style={{ gridTemplateColumns: `repeat(${rows[0].length}, minmax(0, 1fr))` }}
        >
          {[...rows].reverse().map((row, y) =>
            row.map((value, x) => (
              <div key={`${y}-${x}`} title={`${value.toFixed(3)} mm`} style={{ background: meshColour(value, scale) }} />
            )),
          )}
        </div>
      )}
      {askCalibrate && (
        <ConfirmModal
          title={t('printers.klipper.meshCalibrate', 'Calibrate')}
          message={t(
            'printers.klipper.meshCalibrateMessage',
            'Home and probe a new bed mesh (G28, BED_MESH_CALIBRATE)? It stays active until the next restart: SAVE_CONFIG keeps it.',
          )}
          confirmText={t('printers.klipper.meshCalibrate', 'Calibrate')}
          variant="warning"
          onConfirm={() => {
            calibrate.mutate();
            setAskCalibrate(false);
          }}
          onCancel={() => setAskCalibrate(false)}
        />
      )}
    </div>
  );
}

/** Klipper's live tuning (flow, pressure advance), the bed mesh and the versions, in the manual controls. */
export function KlipperTuning({ printerId, status, showToast }: Props) {
  const { t } = useTranslation();
  const [flow, setFlow] = useState('');
  const [pa, setPa] = useState('');
  const tuning = status?.klipper_tuning;
  const printing = status?.state === 'RUNNING' || status?.state === 'PAUSE';
  const onError = (error: Error) => showToast(error.message, 'error');
  const flowMut = useMutation({
    mutationFn: (percent: number) => api.klipperSetFlow(printerId, percent),
    onSuccess: () => setFlow(''),
    onError,
  });
  const paMut = useMutation({
    mutationFn: (value: number) => api.klipperSetPressureAdvance(printerId, value),
    onSuccess: () => setPa(''),
    onError,
  });
  const versions = useQuery({
    queryKey: ['klipperVersions', printerId],
    queryFn: () => api.klipperVersions(printerId),
    enabled: status?.connected !== false,
    staleTime: 10 * 60_000,
  });

  return (
    <div className="flex flex-col gap-3 w-full">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <label className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
          {t('printers.klipper.flow', 'Flow')}
          <input
            type="number"
            aria-label={t('printers.klipper.flow', 'Flow')}
            className={input}
            min={50}
            max={150}
            value={flow}
            placeholder={tuning?.flow != null ? String(tuning.flow) : '—'}
            onChange={(e) => setFlow(e.target.value)}
          />
          %
          <button className={btn} disabled={flow === '' || flowMut.isPending} onClick={() => flowMut.mutate(Number(flow))}>
            Set
          </button>
        </label>
        <label className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
          {t('printers.klipper.pressureAdvance', 'Pressure advance')}
          <input
            type="number"
            aria-label={t('printers.klipper.pressureAdvance', 'Pressure advance')}
            className={input}
            min={0}
            max={1}
            step={0.001}
            value={pa}
            placeholder={tuning?.pressure_advance != null ? String(tuning.pressure_advance) : '—'}
            onChange={(e) => setPa(e.target.value)}
          />
          <button className={btn} disabled={pa === '' || paMut.isPending} onClick={() => paMut.mutate(Number(pa))}>
            Set
          </button>
        </label>
      </div>
      <BedMesh printerId={printerId} printing={printing} showToast={showToast} />
      {versions.data && (
        <p className="text-[10px] text-[var(--text-muted)] font-mono break-all">
          Klipper {versions.data.klipper ?? '?'} · Moonraker {versions.data.moonraker ?? '?'}
        </p>
      )}
    </div>
  );
}
