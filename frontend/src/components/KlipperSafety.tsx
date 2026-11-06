import { useState } from 'react';
import { AlertTriangle, OctagonX, RotateCcw } from 'lucide-react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../api/client';
import type { PrinterStatus } from '../api/client';
import { ConfirmModal } from './ConfirmModal';

type ToastType = 'success' | 'error' | 'warning' | 'info' | 'loading';

interface Props {
  printerId: number;
  status?: Partial<Pick<PrinterStatus, 'klipper' | 'state'>>;
  showToast: (msg: string, type?: ToastType) => void;
}

/** FIRMWARE_RESTART after a confirm; during a print the backend asks a second, explicit confirm (409). */
function useFirmwareRestart(printerId: number, showToast: Props['showToast']) {
  const { t } = useTranslation();
  const [asking, setAsking] = useState<'restart' | 'printing' | null>(null);
  const restart = useMutation({
    mutationFn: (confirm: boolean) => api.klipperFirmwareRestart(printerId, confirm),
    onSuccess: () => showToast(t('printers.klipper.restartSent', 'Firmware restart sent'), 'success'),
    onError: (error: Error) => {
      if (error instanceof ApiError && error.code === 'restart_confirm_while_printing') setAsking('printing');
      else showToast(error.message, 'error');
    },
  });
  const modal = asking && (
    <ConfirmModal
      title={t('printers.klipper.restartTitle', 'Restart the firmware')}
      message={
        asking === 'printing'
          ? t('printers.klipper.restartWhilePrinting', 'The printer is printing: the restart ends the print. Restart anyway?')
          : t('printers.klipper.restartMessage', 'Restart Klipper and its boards (FIRMWARE_RESTART)?')
      }
      confirmText={t('printers.klipper.restart', 'Restart firmware')}
      variant={asking === 'printing' ? 'danger' : 'warning'}
      onConfirm={() => {
        restart.mutate(asking === 'printing');
        setAsking(null);
      }}
      onCancel={() => setAsking(null)}
    />
  );
  return { ask: () => setAsking('restart'), pending: restart.isPending, modal };
}

/** On the printer card: Klipper not ready (shutdown, error...) with its message, or a pause for filament runout. */
export function KlipperAlert({ printerId, status, showToast }: Props) {
  const { t } = useTranslation();
  const restart = useFirmwareRestart(printerId, showToast);
  const klipper = status?.klipper;
  if (!klipper) return null;
  if (klipper.state !== 'ready' && klipper.state !== 'startup') {
    return (
      <div role="alert" className="mt-2 rounded-lg border border-red-400/40 bg-red-500/10 p-2 text-xs text-red-300">
        <div className="flex items-center gap-2 font-semibold text-red-400">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          {t('printers.klipper.notReady', { state: klipper.state, defaultValue: 'Klipper {{state}}' })}
        </div>
        {klipper.message && <p className="mt-1 whitespace-pre-line break-words max-h-24 overflow-y-auto">{klipper.message}</p>}
        <button
          type="button"
          className="mt-2 inline-flex items-center gap-1 px-2 py-1 rounded border border-red-400/60 text-red-300 hover:bg-red-500/20 disabled:opacity-50"
          disabled={restart.pending}
          onClick={restart.ask}
        >
          <RotateCcw className="w-3 h-3" />
          {t('printers.klipper.restart', 'Restart firmware')}
        </button>
        {restart.modal}
      </div>
    );
  }
  if (klipper.filament_runout) {
    return (
      <div role="alert" className="mt-2 flex items-center gap-2 rounded-lg border border-yellow-400/40 bg-yellow-500/10 p-2 text-xs text-yellow-300">
        <AlertTriangle className="w-4 h-4 shrink-0" />
        {t('printers.klipper.runout', 'Paused: filament runout. Load the filament, then resume.')}
      </div>
    );
  }
  return null;
}

/** In the manual controls: emergency stop (M112) and firmware restart. */
export function KlipperSafetyControls({ printerId, showToast }: Omit<Props, 'status'>) {
  const { t } = useTranslation();
  const [askStop, setAskStop] = useState(false);
  const restart = useFirmwareRestart(printerId, showToast);
  const stop = useMutation({
    mutationFn: () => api.klipperEmergencyStop(printerId),
    onSuccess: () => showToast(t('printers.klipper.emergencyStopSent', 'Emergency stop sent'), 'warning'),
    onError: (error: Error) => showToast(error.message, 'error'),
  });
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        className="flex items-center justify-center gap-1 px-3 py-1.5 rounded text-sm font-medium bg-red-600 hover:bg-red-700 text-white disabled:opacity-40"
        disabled={stop.isPending}
        onClick={() => setAskStop(true)}
      >
        <OctagonX size={14} />
        {t('printers.klipper.emergencyStop', 'Emergency stop')}
      </button>
      <button
        type="button"
        className="flex items-center justify-center gap-1 px-3 py-1.5 rounded text-sm font-medium border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-white disabled:opacity-40"
        disabled={restart.pending}
        onClick={restart.ask}
      >
        <RotateCcw size={14} />
        {t('printers.klipper.restart', 'Restart firmware')}
      </button>
      {askStop && (
        <ConfirmModal
          title={t('printers.klipper.emergencyStop', 'Emergency stop')}
          message={t(
            'printers.klipper.emergencyStopMessage',
            'Heaters and motors off at once (M112): the print is lost and Klipper stays stopped until a firmware restart. Stop now?',
          )}
          confirmText={t('printers.klipper.emergencyStop', 'Emergency stop')}
          variant="danger"
          onConfirm={() => {
            stop.mutate();
            setAskStop(false);
          }}
          onCancel={() => setAskStop(false)}
        />
      )}
      {restart.modal}
    </div>
  );
}
