import { useEffect, useRef, useState } from 'react';
import { Play, Send, Terminal } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import { ConfirmModal } from './ConfirmModal';

type ToastType = 'success' | 'error' | 'warning' | 'info' | 'loading';

interface Props {
  printerId: number;
  showToast: (msg: string, type?: ToastType) => void;
}

/** Klipper macros (run after a confirm) and the G-code console (Moonraker's gcode_store, polled while shown). */
export function KlipperMacrosConsole({ printerId, showToast }: Props) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [macroToRun, setMacroToRun] = useState<string | null>(null);
  const [command, setCommand] = useState('');
  const logRef = useRef<HTMLDivElement>(null);
  const consoleKey = ['klipperConsole', printerId];

  const macros = useQuery({
    queryKey: ['klipperMacros', printerId],
    queryFn: () => api.klipperMacros(printerId),
    staleTime: 60_000,
  });
  const consoleLines = useQuery({
    queryKey: consoleKey,
    queryFn: () => api.klipperConsole(printerId, 100),
    refetchInterval: 2000,
  });

  const refreshConsole = () => queryClient.invalidateQueries({ queryKey: consoleKey });
  const runMacro = useMutation({
    mutationFn: (name: string) => api.klipperRunMacro(printerId, name),
    onSuccess: (_, name) => showToast(t('printers.klipper.macroSent', { name, defaultValue: '{{name}} sent' }), 'success'),
    onError: (error: Error) => showToast(error.message, 'error'),
    onSettled: refreshConsole,
  });
  const sendCommand = useMutation({
    mutationFn: (script: string) => api.klipperConsoleSend(printerId, script),
    onSuccess: () => setCommand(''),
    onError: (error: Error) => showToast(error.message, 'error'),
    onSettled: refreshConsole,
  });

  const lines = consoleLines.data?.lines ?? [];
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [lines.length]);

  const send = () => {
    if (command.trim() && !sendCommand.isPending) sendCommand.mutate(command.trim());
  };
  const lineClass = (line: (typeof lines)[number]) =>
    line.type === 'command'
      ? 'text-[var(--accent)]'
      : line.message.startsWith('!!')
        ? 'text-red-400'
        : 'text-[var(--text-secondary)]';

  return (
    <div className="flex flex-col gap-3 w-full">
      <div className="flex flex-col gap-1.5">
        <span className="text-xs text-[var(--text-muted)]">{t('printers.klipper.macros', 'Macros')}</span>
        {macros.isError ? (
          <span className="text-xs text-red-400">{t('printers.klipper.macrosUnavailable', 'Macros unavailable')}</span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {(macros.data?.macros ?? []).map((name) => (
              <button
                key={name}
                type="button"
                className="flex items-center gap-1 px-2 py-1 rounded text-xs font-mono border border-[var(--border-color)] hover:border-[var(--accent)] hover:text-[var(--accent)] text-[var(--text-secondary)] transition-colors disabled:opacity-40"
                disabled={runMacro.isPending}
                onClick={() => setMacroToRun(name)}
              >
                <Play size={10} />
                {name}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="flex items-center gap-1 text-xs text-[var(--text-muted)]">
          <Terminal size={12} />
          {t('printers.klipper.console', 'Console')}
        </span>
        <div
          ref={logRef}
          role="log"
          aria-label={t('printers.klipper.console', 'Console')}
          className="h-40 overflow-y-auto rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] p-2 font-mono text-[11px] leading-snug"
        >
          {lines.map((line, index) => (
            <div key={`${line.time}-${index}`} className={`whitespace-pre-wrap break-words ${lineClass(line)}`}>
              <span className="text-[var(--text-muted)]">{new Date(line.time * 1000).toLocaleTimeString()} </span>
              {line.message}
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            aria-label={t('printers.klipper.consoleCommand', 'G-code command')}
            className="flex-1 min-w-0 rounded px-2 py-1 text-sm font-mono bg-[var(--bg-primary)] border border-[var(--border-color)] focus:outline-none focus:border-[var(--accent)]"
            placeholder="G28, QUERY_ENDSTOPS…"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') send();
            }}
          />
          <button
            type="button"
            aria-label={t('printers.klipper.consoleSend', 'Send')}
            className="flex items-center justify-center px-3 rounded border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent)] hover:text-white transition-colors disabled:opacity-40"
            disabled={!command.trim() || sendCommand.isPending}
            onClick={send}
          >
            <Send size={14} />
          </button>
        </div>
      </div>

      {macroToRun && (
        <ConfirmModal
          title={t('printers.klipper.runMacroTitle', 'Run macro')}
          message={t('printers.klipper.runMacroMessage', {
            name: macroToRun,
            defaultValue: 'Run {{name}} on the printer now?',
          })}
          confirmText={t('printers.klipper.runMacro', 'Run')}
          variant="warning"
          onConfirm={() => {
            runMacro.mutate(macroToRun);
            setMacroToRun(null);
          }}
          onCancel={() => setMacroToRun(null)}
        />
      )}
    </div>
  );
}
