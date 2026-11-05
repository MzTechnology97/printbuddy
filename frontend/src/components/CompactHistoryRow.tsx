import {
  CheckCircle,
  XCircle,
  SkipForward,
  X,
  RefreshCw,
  Trash2,
  Printer,
  Timer,
  Layers,
} from 'lucide-react';
import { api } from '../api/client';
import { type TimeFormat, formatDuration, formatRelativeTime } from '../utils/date';
import type { PrintQueueItem, Permission } from '../api/client';
import { Button } from './Button';

const STATUS_CONFIG = {
  completed: { icon: CheckCircle, color: 'text-emerald-400', border: 'border-l-emerald-500' },
  failed: { icon: XCircle, color: 'text-red-400', border: 'border-l-red-500' },
  skipped: { icon: SkipForward, color: 'text-orange-400', border: 'border-l-gray-500' },
  cancelled: { icon: X, color: 'text-gray-400', border: 'border-l-gray-500' },
} as const;

export function CompactHistoryRow({
  item,
  onRequeue,
  onRemove,
  timeFormat = 'system',
  hasPermission,
  canModify,
  t,
}: {
  item: PrintQueueItem;
  onRequeue: () => void;
  onRemove: () => void;
  timeFormat?: TimeFormat;
  hasPermission: (permission: Permission) => boolean;
  canModify: (resource: 'queue' | 'archives' | 'library', action: 'update' | 'delete' | 'reprint', createdById: number | null | undefined) => boolean;
  t: (key: string, options?: Record<string, unknown>) => string;
}) {
  const config = STATUS_CONFIG[item.status as keyof typeof STATUS_CONFIG] || STATUS_CONFIG.cancelled;
  const StatusIcon = config.icon;
  const displayName = item.archive_name || item.library_file_name || `File #${item.archive_id || item.library_file_id}`;

  const thumbnailUrl = item.archive_thumbnail
    ? api.getArchiveThumbnail(item.archive_id!)
    : item.library_file_thumbnail
      ? api.getLibraryFileThumbnailUrl(item.library_file_id!)
      : null;

  const completedTime = item.completed_at || item.created_at;

  const failure = failureInfo(item, t);

  return (
    <div className={`px-3 py-2 bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary border-l-[3px] ${config.border}`}>
      <div className="flex items-center gap-2 sm:gap-3">
        {/* Status icon */}
        <StatusIcon className={`w-4 h-4 shrink-0 ${config.color}`} />

        {/* Thumbnail */}
        <div className="w-8 h-8 shrink-0 bg-bambu-dark rounded overflow-hidden">
          {thumbnailUrl ? (
            <img src={thumbnailUrl} alt="" className="w-full h-full object-cover" />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-bambu-gray">
              <Layers className="w-4 h-4" />
            </div>
          )}
        </div>

        {/* File name */}
        <span className="text-sm text-white font-medium truncate min-w-0 flex-1">
          {displayName}
        </span>

        {/* Printer */}
        {item.printer_name && (
          <span className="hidden sm:flex items-center gap-1 text-xs text-bambu-gray shrink-0">
            <Printer className="w-3 h-3" />
            <span className="truncate max-w-[100px]">{item.printer_name}</span>
          </span>
        )}

        {/* Duration */}
        {item.print_time_seconds && (
          <span className="hidden sm:flex items-center gap-1 text-xs text-bambu-gray shrink-0">
            <Timer className="w-3 h-3" />
            {formatDuration(item.print_time_seconds)}
          </span>
        )}

        {/* Completed time */}
        <span className="text-xs text-bambu-gray shrink-0">
          {formatRelativeTime(completedTime, timeFormat, t)}
        </span>

        {/* Actions */}
        <div className="flex items-center gap-0.5 shrink-0">
          <Button
            variant="ghost"
            size="sm"
            onClick={onRequeue}
            disabled={!hasPermission('queue:create')}
            title={!hasPermission('queue:create') ? t('queue.permissions.noRequeue') : t('queue.actions.requeue')}
            className="text-bambu-green hover:text-bambu-green/80 hover:bg-bambu-green/10 p-1.5"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onRemove}
            disabled={!canModify('queue', 'delete', item.created_by_id)}
            title={!canModify('queue', 'delete', item.created_by_id) ? t('queue.permissions.noRemove') : t('common.remove')}
            className="p-1.5"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>
      {failure && (
        <div className="mt-1.5 pl-6 sm:pl-[3.25rem] text-xs space-y-0.5" data-testid="history-failure">
          <p className={item.status === 'failed' ? 'text-red-400' : 'text-bambu-gray'}>{failure.reason}</p>
          {failure.meta.length > 0 && <p className="text-bambu-gray">{failure.meta.join(' · ')}</p>}
          {failure.technical.length > 0 && (
            <details className="text-bambu-gray">
              <summary className="cursor-pointer select-none hover:text-white">{t('queue.failure.technical')}</summary>
              <pre className="mt-1 p-2 bg-bambu-dark rounded whitespace-pre-wrap break-all font-mono text-[11px]">
                {failure.technical.join('\n')}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

// fork: why a history item failed or was stopped, where, and what the printer or the upload said
function failureInfo(
  item: PrintQueueItem,
  t: (key: string, options?: Record<string, unknown>) => string,
): { reason: string; meta: string[]; technical: string[] } | null {
  const detail = item.failure_detail ?? {};
  if (item.status !== 'failed' && !(item.status === 'cancelled' && (item.error_message || item.failure_detail))) {
    return null;
  }
  const reason =
    detail.code === 'unknown' || !item.error_message
      ? item.status === 'failed'
        ? t('queue.failure.unknown')
        : t('queue.status.cancelled')
      : item.error_message;
  const meta: string[] = [];
  if (detail.phase) meta.push(t(`queue.failure.phase.${detail.phase}`));
  if (detail.progress != null) meta.push(t('queue.failure.progress', { progress: Math.round(detail.progress) }));
  if (detail.layer != null) {
    meta.push(
      detail.total_layers
        ? t('queue.failure.layerOf', { layer: detail.layer, total: detail.total_layers })
        : t('queue.failure.layer', { layer: detail.layer }),
    );
  }
  if (detail.by) meta.push(t('queue.failure.by', { name: detail.by }));
  if (detail.via) meta.push(t(`queue.failure.via.${detail.via}`));
  if (item.dispatch_attempts) meta.push(t('queue.failure.attempts', { n: item.dispatch_attempts }));
  return { reason, meta, technical: detail.technical ?? [] };
}
