// Printfarm (fork) products API: see backend/app/farm/products/routes.py.
import { getAuthToken } from '../api/client';

export interface FarmPlate {
  library_file_id: number;
  plate: number;
  printer_model: string;
  material: string;
  yield: number;
}

export interface FarmPart {
  component_code: string;
  name: string;
  quantity: number;
  plates: FarmPlate[];
}

export interface FarmProduct {
  project_id: number;
  name: string;
  skus: string[];
  asins: string[];
  updated_at: string;
  parts: FarmPart[];
}

export interface FarmComponent {
  code: string;
  name: string;
  shape_id: number | null;
  shape: string | null;
  color: string | null;
  retired: boolean;
}

export interface FarmPlateSummary {
  id: number;
  library_file_id: number;
  plate_index: number;
  status: string;
  yields: { object_name: string; quantity: number; shape_id: number | null; component_code: string | null }[];
}

export interface FarmPlateDetail {
  id: number;
  library_file_id: number;
  plate_index: number;
  name: string | null;
  printer_model: string | null;
  target_model: string | null;
  materials: string[];
  nozzle_diameter: number | null;
  print_time_seconds: number | null;
  filament_grams: number | null;
  sliced_file_id: number | null;
  status: string;
  warning: string | null;
  yields: {
    object_name: string;
    quantity: number;
    shape_id: number | null;
    suggested_shape_id: number | null;
    component_code: string | null;
  }[];
}

export interface FarmLibraryFileState {
  library_file_id: number | null;
  path: string;
  status: string;
  slicer: string | null;
  printer_model: string | null;
}

export interface FarmProductView {
  project_id: number;
  skus: string[];
  asins: string[];
  parts: (FarmComponent & {
    quantity: number;
    plates: {
      plate_id: number;
      library_file_id: number;
      file: string;
      plate: number;
      printer_model: string | null;
      material: string;
      yield: number;
      print_time_seconds: number | null;
      filament_grams: number | null;
      sliced: boolean;
    }[];
  })[];
  files: {
    library_file_id: number;
    filename: string;
    path: string;
    folder: string;
    proposed: number;
    confirmed: number;
    opened: boolean;
  }[];
}

export interface FarmPlanPlate {
  plate_id: number;
  library_file_id: number;
  file: string;
  plate: number;
  printer_model: string;
  material: string;
  yield: number;
  print_time_seconds: number | null;
  filament_grams: number | null;
  sliced: boolean;
  score: number;
  printers: { id: number; name: string; idle: boolean; material_ok: boolean | null; color_ok: boolean | null }[];
}

export interface FarmPlanComponent {
  code: string;
  name: string;
  status: string;
  to_print: number | null;
  stock: number | null;
  need: number | null;
  days_left: number | null;
  color: string | null;
  color_hex: string | null;
  shape: string | null;
  shape_id: number | null;
  known: boolean;
  in_queue: number;
  missing: number;
  plates: FarmPlanPlate[];
}

export interface FarmJob {
  id: number;
  component_code: string;
  color: string | null;
  state: 'waiting' | 'pending' | 'printing' | 'completed' | 'failed' | 'cancelled' | 'skipped' | 'removed';
  note: string | null;
  plate_ids: number[];
  plate_id: number | null;
  printer_id: number | null;
  printer: string | null;
  queue_item_id: number | null;
  pieces: number;
  created_at: string;
}

export interface FarmPlanSettings {
  url: string;
  tolerance: number;
  colors: Record<string, string>;
}

export interface FarmShape {
  id: number;
  name: string;
}

/** One distinct piece across all plates (same geometry, any object name). */
export interface FarmPiece {
  yield_ids: number[];
  names: string[];
  objects: number;
  plates: number;
  files: string[];
  file_count: number;
  shape_id: number | null;
  mixed: boolean;
  unlinked: number;
  suggested_shape_id: number | null;
  volume_mm3: number;
  hand: number;
  sample: { library_file_id: number; plate_index: number };
}

export interface FarmProductOverview {
  project_id: number;
  has_cover: boolean;
  queued: number;
  printing: number;
  printed_recent: number;
}

export interface FarmProductInput {
  name?: string;
  project_id?: number;
  skus: string[];
  asins: string[];
  parts: { component_code: string; quantity: number }[];
}

export async function farmRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getAuthToken();
  const response = await fetch(`/api/v1/farm${path}`, {
    ...init,
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const detail = Array.isArray(error.detail) ? error.detail.map((d: { msg?: string }) => d.msg).join('; ') : error.detail;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response.json();
}

export const farmApi = {
  getProducts: () => farmRequest<{ generated_at: string; products: FarmProduct[] }>('/products'),
  getOverview: () => farmRequest<FarmProductOverview[]>('/products/overview'),
  getComponents: () => farmRequest<FarmComponent[]>('/components'),
  createProduct: (data: FarmProductInput) =>
    farmRequest<{ project_id: number }>('/products', { method: 'POST', body: JSON.stringify(data) }),
  updateProduct: (id: number, data: FarmProductInput) =>
    farmRequest<{ project_id: number }>(`/products/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  unmarkProduct: (id: number) => farmRequest<{ project_id: number }>(`/products/${id}`, { method: 'DELETE' }),
  createComponent: (data: { name?: string; shape_id?: number; color?: string }) =>
    farmRequest<FarmComponent>('/components', { method: 'POST', body: JSON.stringify(data) }),
  updateComponent: (
    code: string,
    data: { name?: string; retired?: boolean; shape_id?: number | null; color?: string | null },
  ) => farmRequest<FarmComponent>(`/components/${code}`, { method: 'PATCH', body: JSON.stringify(data) }),
  mergeComponent: (code: string, into: string) =>
    farmRequest<{ merged: string; into: string; products: number }>(`/components/${code}/merge`, {
      method: 'POST',
      body: JSON.stringify({ into }),
    }),
  getShapes: () => farmRequest<FarmShape[]>('/shapes'),
  getPlan: () =>
    farmRequest<{ fetched_at: string | null; error: string | null; tolerance: number; components: FarmPlanComponent[] }>(
      '/plan',
    ),
  getJobs: () => farmRequest<FarmJob[]>('/jobs'),
  createJobs: (jobs: { component_code: string; plate_ids: number[] }[]) =>
    farmRequest<FarmJob[]>('/jobs', { method: 'POST', body: JSON.stringify({ jobs }) }),
  cancelJob: (id: number) => farmRequest<{ id: number; state: string }>(`/jobs/${id}`, { method: 'DELETE' }),
  getPlanSettings: () => farmRequest<FarmPlanSettings>('/plan/settings'),
  savePlanSettings: (data: FarmPlanSettings) =>
    farmRequest<FarmPlanSettings>('/plan/settings', { method: 'PUT', body: JSON.stringify(data) }),
  getProjectNames: () => farmRequest<{ id: number; name: string }[]>('/projects'),
  renameShape: (id: number, name: string) =>
    farmRequest<FarmShape>(`/shapes/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  getProductView: (projectId: number) => farmRequest<FarmProductView>(`/products/${projectId}/view`),
  getFarmFiles: () => farmRequest<FarmLibraryFileState[]>('/library/files?status=ok'),
  getPlatesSummary: () =>
    farmRequest<{ library_file_id: number; proposed: number; confirmed: number }[]>('/plates/summary'),
  getFilePlates: (libraryFileId: number) => farmRequest<FarmPlateDetail[]>(`/library/files/${libraryFileId}/plates`),
  confirmPlate: (plateId: number, yields: { object_name: string; quantity: number; shape_id: number | null }[]) =>
    farmRequest<FarmPlateDetail>(`/plates/${plateId}`, { method: 'PUT', body: JSON.stringify({ yields }) }),
  getPlates: (status?: string) =>
    farmRequest<FarmPlateSummary[]>(`/plates${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  getFolderProjects: (folderId: number) =>
    farmRequest<{ folder_id: number; project_ids: number[] }>(`/folders/${folderId}/projects`),
  setFolderProjects: (folderId: number, projectIds: number[]) =>
    farmRequest<{ folder_id: number; project_ids: number[] }>(`/folders/${folderId}/projects`, {
      method: 'PUT',
      body: JSON.stringify({ project_ids: projectIds }),
    }),
  createShape: (name: string) => farmRequest<FarmShape>('/shapes', { method: 'POST', body: JSON.stringify({ name }) }),
  getPieces: () => farmRequest<FarmPiece[]>('/pieces'),
  linkPiece: (yieldIds: number[], shapeId: number | null) =>
    farmRequest<{ linked: number; plates_confirmed: number }>('/pieces', {
      method: 'PUT',
      body: JSON.stringify({ yield_ids: yieldIds, shape_id: shapeId }),
    }),
};
