// Printfarm (fork): Accoda jobs that are not in the print queue yet (their plate is being sliced, or no
// printer fits), shown on the Queue page so a queued print is visible from the start, with its slicing %.
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { farmApi, type FarmJob } from './api';
import './i18n';

function SlicingStatus({ job }: { job: FarmJob }) {
  const { t } = useTranslation();
  const s = job.slicing;
  if (!s) return <span className="text-xs text-amber-400 break-words">{job.note || t('farm.jobs.state.waiting')}</span>;
  if (s.status === 'failed') {
    return <span className="text-xs text-red-400 break-words">{t('farm.waiting.sliceFailed', { error: s.error || '' })}</span>;
  }
  return (
    <div className="flex items-center gap-2 min-w-[10rem] flex-1">
      <Loader2 className="w-3.5 h-3.5 text-bambu-green animate-spin shrink-0" />
      <span className="text-xs text-white whitespace-nowrap">
        {s.status === 'pending'
          ? t('farm.waiting.sliceQueued')
          : s.percent != null
            ? t('farm.waiting.slicing', { percent: s.percent })
            : t('farm.waiting.slicingStarted')}
      </span>
      {s.percent != null && (
        <div
          className="h-1.5 flex-1 bg-bambu-dark rounded-full overflow-hidden"
          role="progressbar"
          aria-valuenow={s.percent}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="h-full bg-bambu-green" style={{ width: `${s.percent}%` }} />
        </div>
      )}
    </div>
  );
}

export function WaitingJobs() {
  const { t } = useTranslation();
  const jobs = useQuery({
    queryKey: ['farm', 'jobs'],
    queryFn: farmApi.getJobs,
    // Fast while something slices, so the % moves; slow otherwise.
    refetchInterval: (q) => (q.state.data?.some((j) => j.slicing && j.slicing.status !== 'failed') ? 3_000 : 30_000),
  });
  const waiting = (jobs.data || []).filter((j) => j.state === 'waiting');
  if (!waiting.length) return null;
  return (
    <section className="mb-6 sm:mb-8">
      <h2 className="text-base sm:text-lg font-semibold text-white mb-3 sm:mb-4 flex items-center gap-2">
        <Loader2 className="w-4 h-4 text-bambu-green" />
        {t('farm.waiting.title')}
        <span className="text-xs sm:text-sm font-normal text-bambu-gray">({waiting.length})</span>
      </h2>
      <ul className="divide-y divide-bambu-dark-tertiary bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
        {waiting.map((j) => (
          <li key={j.id} className="p-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
            <span className="font-mono text-bambu-green">{j.component_code}</span>
            <span className="text-white">{j.color}</span>
            <span className="text-bambu-gray">{t('farm.plan.yield', { count: j.pieces })}</span>
            <span className="text-bambu-gray">· {j.printer || t('farm.jobs.firstFree')}</span>
            <SlicingStatus job={j} />
          </li>
        ))}
      </ul>
    </section>
  );
}
