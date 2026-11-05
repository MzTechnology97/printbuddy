// Printfarm (fork): the queue page's "Jobs" board. One row per print, from the request to the end, in four
// groups; a row opened shows its timeline, the technical details and the log lines, to copy for support.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Circle, Clock, Copy, FileBox, Loader2, Printer as PrinterIcon,
  RefreshCw, RotateCcw, Play, XCircle,
} from 'lucide-react';
import { api } from '../api/client';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { useToast } from '../contexts/ToastContext';
import { copyTextToClipboard } from '../utils/clipboard';
import { appAssetPath } from '../utils/assetPaths';
import { farmApi, type FarmBoardDetail, type FarmBoardEvent, type FarmBoardGroup, type FarmBoardRow } from './api';
import { OutsidePrints } from './OutsidePrints';
import './i18n';

const GROUPS: FarmBoardGroup[] = ['problems', 'printing', 'waiting', 'history'];
const SLICERS: Record<string, { name: string; icon: string }> = {
  orcaslicer: { name: 'OrcaSlicer', icon: '/img/slicers/orcaslicer.png' },
  bambu_studio: { name: 'Bambu Studio', icon: '/img/slicers/bambustudio.png' },
};
// The steps of a print, in order; each lists the event phases that mark it.
const STEPS: { id: string; phases: string[] }[] = [
  { id: 'created', phases: ['created'] },
  { id: 'slice', phases: ['slice_requested', 'slice_done', 'slice_failed'] },
  { id: 'queued', phases: ['queued'] },
  { id: 'send', phases: ['upload_started', 'upload_done', 'upload_failed', 'dispatch_failed'] },
  { id: 'start', phases: ['started', 'start_failed'] },
  { id: 'print', phases: [] },
  { id: 'end', phases: ['finished', 'failed', 'cancelled'] },
];

// A print from before the event log has no events: its step comes from its state, and where it failed.
const PHASE_STEP: Record<string, number> = {
  waiting: 1, slicing: 1, slice_failed: 1, queued: 2, sending: 3, printing: 5, finished: 6, failed: 6, cancelled: 6,
};
const FAILED_AT: Record<string, number> = { dispatch: 2, upload: 3, start: 4, printing: 5 };

const time = (iso: string | null) =>
  iso ? new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`).toLocaleString([], {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }) : '';

export function SlicerBadge({ slicer }: { slicer: string | null }) {
  const s = slicer ? SLICERS[slicer] : undefined;
  if (!s) return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-bambu-gray" title={s.name}>
      <img src={appAssetPath(s.icon)} alt={s.name} className="w-4 h-4" />
      <span className="hidden sm:inline">{s.name}</span>
    </span>
  );
}

function RowIcon({ row }: { row: FarmBoardRow }) {
  if (row.group === 'problems') return <XCircle className="w-4 h-4 text-red-400 shrink-0" />;
  if (row.group === 'printing') return <Loader2 className="w-4 h-4 text-bambu-green animate-spin shrink-0" />;
  if (row.group === 'waiting') return <Clock className="w-4 h-4 text-amber-400 shrink-0" />;
  if (row.phase === 'finished') return <CheckCircle2 className="w-4 h-4 text-bambu-green shrink-0" />;
  return <Circle className="w-4 h-4 text-bambu-gray shrink-0" />;
}

function Progress({ printerId }: { printerId: number }) {
  const { t } = useTranslation();
  const { data } = useQuery({
    queryKey: ['printerStatus', printerId],
    queryFn: () => api.getPrinterStatus(printerId),
    refetchInterval: 5000,
  });
  if (!data || data.progress == null) return null;
  const left = data.remaining_time
    ? `${Math.floor(data.remaining_time / 60)}h${String(data.remaining_time % 60).padStart(2, '0')}`
    : '';
  return (
    <span className="text-xs text-bambu-green">
      {Math.round(data.progress)}%
      {data.layer_num ? ` · ${t('farm.board.layer', { n: data.layer_num, of: data.total_layers ?? '?' })}` : ''}
      {left ? ` · ${left}` : ''}
    </span>
  );
}

/** The steps done, the one now and the one that failed, with the events under each (ⓘ: their raw details). */
function Timeline({ row, events }: { row: FarmBoardRow; events: FarmBoardEvent[] }) {
  const { t } = useTranslation();
  const [raw, setRaw] = useState<number | null>(null);
  const stepOf = (phase: string) => STEPS.findIndex((s) => s.phases.includes(phase));
  const errors = events.some((e) => e.level === 'error' && stepOf(e.phase) >= 0);
  // shortcut: a print that failed without an event (older than the log) is pinned by its state only
  const failedAt = row.group === 'problems' && !errors
    ? (FAILED_AT[row.failure_phase ?? ''] ?? PHASE_STEP[row.phase] ?? -1) : -1;
  const fromState = row.group === 'problems' ? failedAt : (PHASE_STEP[row.phase] ?? -1);
  const reached = Math.max(-1, ...events.map((e) => stepOf(e.phase)), fromState);
  const actions = events.filter((e) => e.phase === 'action');
  return (
    <ol className="space-y-2" aria-label={t('farm.board.timeline')}>
      {STEPS.map((step, i) => {
        const own = events.filter((e) => step.phases.includes(e.phase));
        // a later success clears it
        const failed = (own.length > 0 && own[own.length - 1].level === 'error') || i === failedAt;
        const current = i === reached && row.group !== 'history' && !failed;
        const state = failed ? 'failed' : current ? 'current' : i <= reached ? 'done' : 'todo';
        return (
          <li key={step.id} data-state={state} className="flex gap-2">
            <span className="mt-0.5">
              {state === 'failed' ? <XCircle className="w-4 h-4 text-red-400" />
                : state === 'done' ? <CheckCircle2 className="w-4 h-4 text-bambu-green" />
                : state === 'current' ? <Loader2 className="w-4 h-4 text-sky-400 animate-spin" />
                : <Circle className="w-4 h-4 text-bambu-gray/50" />}
            </span>
            <div className="min-w-0 flex-1">
              <p className={`text-sm ${state === 'todo' ? 'text-bambu-gray/60' : 'text-white'}`}>
                {t(`farm.board.step.${step.id}`)}
                {step.id === 'print' && row.group === 'printing' && row.printer_id ? (
                  <> <Progress printerId={row.printer_id} /></>
                ) : null}
              </p>
              {own.map((e) => {
                const n = events.indexOf(e);
                const colour = e.level === 'error' ? 'text-red-300' : e.level === 'warning' ? 'text-amber-300' : 'text-bambu-gray';
                return (
                  <div key={n} className="text-xs">
                    <button type="button" className={`text-left break-words ${colour}`} onClick={() => setRaw(raw === n ? null : n)}>
                      {time(e.at)} · {e.message}{e.detail ? ' ⓘ' : ''}
                    </button>
                    {raw === n && e.detail && (
                      <pre className="mt-1 p-2 bg-bambu-dark rounded text-[11px] text-bambu-gray whitespace-pre-wrap break-all">
                        {JSON.stringify(e.detail, null, 2)}
                      </pre>
                    )}
                  </div>
                );
              })}
            </div>
          </li>
        );
      })}
      {actions.map((e) => (
        <li key={`${e.at}-${e.message}`} className="text-xs text-bambu-gray">{time(e.at)} · {e.message}</li>
      ))}
    </ol>
  );
}

/** Everything about a row as text, for support: what it is, its timeline, the technical details, the log. */
function supportText(d: FarmBoardDetail): string {
  return [
    `${d.row.title} [${d.row.key}] · ${d.row.group} · ${d.row.phase}`,
    d.row.status_text ?? '',
    '',
    '== timeline',
    ...d.events.map((e) => `${e.at} ${e.level.toUpperCase()} ${e.phase}: ${e.message}${e.detail ? ` ${JSON.stringify(e.detail)}` : ''}`),
    '',
    '== technical',
    JSON.stringify(d.technical, null, 2),
    '',
    '== log',
    ...d.log,
  ].join('\n');
}

function Details({ row }: { row: FarmBoardRow }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [confirmStart, setConfirmStart] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ['farm', 'board', row.key],
    queryFn: () => farmApi.getBoardDetail(row.key),
    refetchInterval: 5000,
  });
  const done = (msg: string) => {
    queryClient.invalidateQueries({ queryKey: ['farm'] });
    queryClient.invalidateQueries({ queryKey: ['queue'] });
    showToast(msg, 'success');
  };
  const act = useMutation({
    mutationFn: (a: 'retry-slice' | 'reslice' | 'requeue') => farmApi.boardAction(row.key, a),
    onSuccess: (_, a) => done(t(`farm.board.done.${a}`)),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const cancel = useMutation({
    mutationFn: async () => {
      if (row.job_id) await farmApi.cancelJob(row.job_id);
      else await api.cancelQueueItem(row.queue_item_id!);
    },
    onSuccess: () => done(t('farm.board.done.cancel')),
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const start = useMutation({
    mutationFn: () => api.startQueueItem(row.queue_item_id!),
    onSuccess: () => { setConfirmStart(false); done(t('farm.board.done.start')); },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  if (isLoading || !data) return <Loader2 className="w-5 h-5 animate-spin text-bambu-green" />;
  const has = (a: string) => row.actions.includes(a);
  const busy = act.isPending || cancel.isPending || start.isPending;
  return (
    <div className="space-y-4">
      <Timeline row={row} events={data.events} />
      <div className="flex flex-wrap gap-2">
        {has('retry_slice') && (
          <Button size="sm" disabled={busy} onClick={() => act.mutate('retry-slice')}>
            <RefreshCw className="w-4 h-4" /> {t('farm.board.action.retry_slice')}
          </Button>
        )}
        {has('reslice') && (
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => act.mutate('reslice')}>
            <RotateCcw className="w-4 h-4" /> {t('farm.board.action.reslice')}
          </Button>
        )}
        {has('requeue') && (
          <Button size="sm" disabled={busy} onClick={() => act.mutate('requeue')}>
            <RotateCcw className="w-4 h-4" /> {t('farm.board.action.requeue')}
          </Button>
        )}
        {has('start') && (
          <Button size="sm" disabled={busy} onClick={() => setConfirmStart(true)}>
            <Play className="w-4 h-4" /> {t('farm.board.action.start')}
          </Button>
        )}
        {(has('cancel') || has('cancel_item')) && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => cancel.mutate()}>
            <XCircle className="w-4 h-4" /> {t('farm.board.action.cancel')}
          </Button>
        )}
        {has('open_file') && row.library_file_id && (
          <Button size="sm" variant="ghost" onClick={() => api.downloadLibraryFile(row.library_file_id!, row.file ?? undefined)
            .catch((e: Error) => showToast(e.message, 'error'))}>
            <FileBox className="w-4 h-4" /> {t('farm.board.action.open_file')}
          </Button>
        )}
      </div>
      <details className="bg-bambu-dark rounded-lg p-3" open={row.group === 'problems'}>
        <summary className="cursor-pointer text-sm text-white">{t('farm.board.technical')}</summary>
        <div className="mt-2">
          <Button size="sm" variant="secondary" onClick={async () => {
            const ok = await copyTextToClipboard(supportText(data));
            showToast(t(ok ? 'farm.board.copied' : 'farm.board.copyFailed'), ok ? 'success' : 'error');
          }}>
            <Copy className="w-4 h-4" /> {t('farm.board.copy')}
          </Button>
        </div>
        <pre className="mt-2 text-[11px] text-bambu-gray whitespace-pre-wrap break-all max-h-80 overflow-auto">
          {JSON.stringify(data.technical, null, 2)}
        </pre>
        <p className="mt-2 text-xs text-white">{t('farm.board.log', { count: data.log.length })}</p>
        <pre className="mt-1 text-[11px] text-bambu-gray whitespace-pre-wrap break-all max-h-60 overflow-auto">
          {data.log.join('\n') || t('farm.board.noLog')}
        </pre>
      </details>
      {confirmStart && (
        <ConfirmModal title={t('farm.board.startTitle')} message={t('farm.board.startMessage', { printer: row.printer ?? '' })}
          confirmText={t('farm.board.action.start')} isLoading={start.isPending}
          onConfirm={() => start.mutate()} onCancel={() => setConfirmStart(false)} />
      )}
    </div>
  );
}

function Row({ row }: { row: FarmBoardRow }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const frame = row.group === 'problems' ? 'border-red-600/40 bg-red-600/5' : 'border-bambu-dark-tertiary bg-bambu-dark-secondary';
  return (
    <li className={`border rounded-lg ${frame}`}>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        className="w-full text-left p-3 flex flex-wrap items-center gap-x-3 gap-y-1">
        {open ? <ChevronDown className="w-4 h-4 text-bambu-gray" /> : <ChevronRight className="w-4 h-4 text-bambu-gray" />}
        <RowIcon row={row} />
        <span className="text-white font-medium break-words min-w-0">{row.title}</span>
        {row.pieces ? <span className="text-xs text-bambu-gray">{t('farm.board.pieces', { count: row.pieces })}</span> : null}
        {row.colour && <span className="text-xs text-bambu-gray">{row.colour}</span>}
        {row.printer && (
          <span className="text-xs text-bambu-gray inline-flex items-center gap-1"><PrinterIcon className="w-3 h-3" />{row.printer}</span>
        )}
        <SlicerBadge slicer={row.slicer} />
        <span className="basis-full sm:basis-auto sm:ml-auto text-xs break-words">
          {row.group === 'printing' && row.printer_id ? <Progress printerId={row.printer_id} /> : (
            <span className={row.group === 'problems' ? 'text-red-300' : 'text-bambu-gray'}>
              {row.slice_progress
                ? t('farm.board.slicingNow', { percent: row.slice_progress.percent ?? '…' })
                : row.status_text || t(`farm.board.phase.${row.phase}`, { defaultValue: row.phase })}
            </span>
          )}
        </span>
      </button>
      {open && <div className="px-3 pb-3"><Details row={row} /></div>}
    </li>
  );
}

export function JobsBoard({ printerId }: { printerId?: number | null }) {
  const { t } = useTranslation();
  const [historyPage, setHistoryPage] = useState(0);
  const [showHistory, setShowHistory] = useState(false);
  const limit = 30;
  const board = useQuery({
    queryKey: ['farm', 'board', printerId ?? null, historyPage],
    queryFn: () => farmApi.getBoard(printerId, historyPage * limit, limit),
    refetchInterval: 5000,
  });
  const jobs = useQuery({ queryKey: ['farm', 'jobs'], queryFn: farmApi.getJobs, refetchInterval: 30_000 });
  if (board.isLoading) return <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />;
  if (board.error || !board.data) return <p className="text-red-400">{(board.error as Error)?.message}</p>;
  const data = board.data;
  const toAssign = (jobs.data || []).some((j) => j.state === 'to_assign');
  return (
    <div className="space-y-6">
      {GROUPS.map((g) => {
        const rows = data[g].filter((r) => r.phase !== 'to_assign'); // those are assigned in the box above
        if (g === 'history') {
          return (
            <section key={g} className="space-y-2">
              <button type="button" className="text-lg font-semibold text-white flex items-center gap-2"
                aria-expanded={showHistory} onClick={() => setShowHistory(!showHistory)}>
                {showHistory ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                {t('farm.board.group.history')} <span className="text-sm text-bambu-gray">({data.history_total})</span>
              </button>
              {showHistory && (
                <>
                  <ul className="space-y-2">{rows.map((r) => <Row key={r.key} row={r} />)}</ul>
                  {data.history_total > limit && (
                    <div className="flex items-center gap-2 text-sm text-bambu-gray">
                      <Button size="sm" variant="secondary" disabled={historyPage === 0}
                        onClick={() => setHistoryPage(historyPage - 1)}>‹</Button>
                      {historyPage + 1} / {Math.ceil(data.history_total / limit)}
                      <Button size="sm" variant="secondary" disabled={(historyPage + 1) * limit >= data.history_total}
                        onClick={() => setHistoryPage(historyPage + 1)}>›</Button>
                    </div>
                  )}
                </>
              )}
            </section>
          );
        }
        const count = rows.length + (g === 'problems' && toAssign ? 1 : 0);
        return (
          <section key={g} className="space-y-2" aria-label={t(`farm.board.group.${g}`)}>
            <h2 className={`text-lg font-semibold flex items-center gap-2 ${g === 'problems' && count ? 'text-red-300' : 'text-white'}`}>
              {g === 'problems' && count ? <AlertTriangle className="w-5 h-5" /> : null}
              {t(`farm.board.group.${g}`)} <span className="text-sm text-bambu-gray">({count})</span>
            </h2>
            {g === 'problems' && toAssign && <OutsidePrints jobs={jobs.data || []} />}
            {rows.length ? (
              <ul className="space-y-2">{rows.map((r) => <Row key={r.key} row={r} />)}</ul>
            ) : !(g === 'problems' && toAssign) && <p className="text-sm text-bambu-gray">{t(`farm.board.empty.${g}`)}</p>}
          </section>
        );
      })}
    </div>
  );
}

/** "What to print": how many jobs are under way; the board itself is in the queue page. */
export function BoardSummary() {
  const { t } = useTranslation();
  const { data } = useQuery({
    queryKey: ['farm', 'board', 'summary'],
    queryFn: () => farmApi.getBoard(null, 0, 1),
    refetchInterval: 30_000,
  });
  if (!data) return null;
  const busy = data.printing.length + data.waiting.length;
  const problems = data.problems.length;
  if (!busy && !problems) return null;
  return (
    <p className="text-sm text-bambu-gray flex flex-wrap items-center gap-2">
      {t('farm.board.summary', { count: busy })}
      {problems ? <span className="text-red-300">· {t('farm.board.summaryProblems', { count: problems })}</span> : null}
      <Link to="/queue?view=jobs" className="text-bambu-green hover:underline">{t('farm.board.toQueue')}</Link>
    </p>
  );
}
