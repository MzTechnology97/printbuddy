// Printfarm (fork): projects that are products; the Projects list leaves them to the Products page.
import { useQuery } from '@tanstack/react-query';
import { farmApi } from './api';

export function useFarmProductIds(): Set<number> {
  const overview = useQuery({ queryKey: ['farm', 'overview'], queryFn: farmApi.getOverview });
  return new Set((overview.data || []).map((o) => o.project_id));
}
