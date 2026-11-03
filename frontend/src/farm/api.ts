// Printfarm (fork) products API: see backend/app/farm/products/routes.py.
import { getAuthToken, withStreamToken } from '../api/client';

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
  kind?: FarmProductKind;
  disabled?: boolean;
  parts: FarmPart[];
  variants?: FarmVariant[];
}

/** Another listing of the product: ``pieces`` units per sale, in ``color``; parts already resolved per sale. */
export interface FarmVariant {
  key: string;
  name: string;
  skus: string[];
  asins: string[];
  color: string | null;
  pieces: number;
  parts: FarmPart[];
}

export interface FarmComponent {
  code: string;
  name: string;
  project_id?: number | null; // its product: components are never shared
  shape_id: number | null;
  shape: string | null;
  color: string | null;
  retired: boolean;
  recipe?: { shape_id: number; shape: string | null; quantity: number }[] | null; // a set printed together
}

export type FarmProductKind = 'set' | 'kit' | null;

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
  name: string;
  has_cover: boolean;
  kind: FarmProductKind;
  disabled: boolean;
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
    used_by: { project_id: number; name: string }[]; // other products with this component
  })[];
  folders: { folder_id: number; name: string }[];
  files: {
    library_file_id: number;
    filename: string;
    path: string;
    folder: string;
    proposed: number;
    confirmed: number;
    opened: boolean;
    excluded: boolean;
    unrelated_plates?: number[]; // plates with only pieces that are not part of the product
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
  printers: {
    id: number;
    name: string;
    idle: boolean;
    material_ok: boolean | null;
    color_ok: boolean | null;
    by_hand?: boolean;
  }[];
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
  products: { project_id: number; name: string; has_cover: boolean }[];
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
  scheduled_time: string | null; // UTC
  created_at: string;
}

/** One print of a component, with upstream's queue options as its print dialog sets them. */
export interface FarmJobIn {
  component_code: string;
  plate_ids: number[];
  printer_id?: number;
  ams_mapping?: number[]; // global tray ids by the plate's filament order: needs printer_id and one plate
  scheduled_time?: string;
  manual_start?: boolean;
  require_previous_success?: boolean;
  auto_off_after?: boolean;
  gcode_injection?: boolean;
}

export interface FarmPrinterSpool {
  printer_id: number;
  name: string;
  model: string;
  material: string | null;
  color_name: string | null;
  color: string | null;
  updated_at: string | null;
  materials?: string[]; // filament types of this model in the slicer profiles
}

export interface FarmPlanSettings {
  url: string;
  tolerance: number;
  colors: Record<string, string>;
}

export interface FarmShape {
  id: number;
  name: string;
  project_id?: number | null; // the product it is a piece of
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

export interface FarmProductPiece {
  yield_ids: number[];
  image_yield_id: number;
  names: string[];
  plates: number;
  files: string[];
  file_count: number;
  printer_models: string[];
  hand: number;
  shape_id: number | null;
  shape: string | null;
  mixed: boolean;
  suggested_shape_id: number | null;
  quantity: number | null;
  not_part?: boolean; // marked "not part of the product": set aside
  component: { code: string; name: string; color: string | null; color_hex: string | null } | null;
}

export interface FarmProductPieces {
  kind: FarmProductKind;
  set: { code: string; name: string; color: string | null; color_hex: string | null } | null;
  default_color: string | null;
  unshaped_parts: { code: string; name: string; quantity: number }[];
  pieces: FarmProductPiece[];
}

export interface FarmProductPieceInput {
  yield_ids: number[];
  quantity: number;
  shape_id?: number;
  shape_name?: string;
  component_code?: string;
  color?: string;
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
  variants?: { skus: string[]; asins: string[]; color: string | null; pieces: number }[];
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
  unmarkProduct: (id: number) =>
    farmRequest<{ project_id: number; retired: string[] }>(`/products/${id}`, { method: 'DELETE' }),
  setProductActive: (id: number, active: boolean) =>
    farmRequest<{ project_id: number; disabled: boolean }>(`/products/${id}/active`, {
      method: 'PUT',
      body: JSON.stringify({ active }),
    }),
  createComponent: (data: { name?: string; shape_id?: number; color?: string }) =>
    farmRequest<FarmComponent>('/components', { method: 'POST', body: JSON.stringify(data) }),
  updateComponent: (
    code: string,
    data: { name?: string; retired?: boolean; shape_id?: number | null; color?: string | null },
  ) => farmRequest<FarmComponent>(`/components/${code}`, { method: 'PATCH', body: JSON.stringify(data) }),
  mergeShape: (shapeId: number, into: number) =>
    farmRequest<{ merged: number; into: number; objects: number; components_merged: string[] }>(
      `/shapes/${shapeId}/merge`,
      { method: 'POST', body: JSON.stringify({ into }) },
    ),
  mergeComponent: (code: string, into: string) =>
    farmRequest<{ merged: string; into: string; products: number }>(`/components/${code}/merge`, {
      method: 'POST',
      body: JSON.stringify({ into }),
    }),
  getShapes: (projectId?: number) =>
    farmRequest<FarmShape[]>(projectId != null ? `/shapes?project_id=${projectId}` : '/shapes'),
  getPlan: () =>
    farmRequest<{ fetched_at: string | null; error: string | null; tolerance: number; components: FarmPlanComponent[] }>(
      '/plan',
    ),
  getProductPlan: (projectId: number) =>
    farmRequest<{ fetched_at: string | null; error: string | null; tolerance: number; components: FarmPlanComponent[] }>(
      `/plan/products/${projectId}`,
    ),
  getJobs: () => farmRequest<FarmJob[]>('/jobs'),
  getPrinterSpools: () => farmRequest<FarmPrinterSpool[]>('/printer-spools'),
  setPrinterSpool: (printerId: number, data: { material: string; color_name: string }) =>
    farmRequest<FarmPrinterSpool>(`/printer-spools/${printerId}`, { method: 'PUT', body: JSON.stringify(data) }),
  clearPrinterSpool: (printerId: number) =>
    farmRequest<{ printer_id: number }>(`/printer-spools/${printerId}`, { method: 'DELETE' }),
  createJobs: (jobs: FarmJobIn[]) =>
    farmRequest<FarmJob[]>('/jobs', { method: 'POST', body: JSON.stringify({ jobs }) }),
  getPlateFilaments: (plateId: number, color?: string | null) =>
    farmRequest<{ plate_id: number; sliced: boolean; filaments: { slot_id: number; type: string; color: string;
      used_grams: number; used_meters?: number; nozzle_id?: number }[] }>(
      `/plates/${plateId}/filaments${color ? `?color=${encodeURIComponent(color)}` : ''}`),
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
  setFileExcluded: (projectId: number, libraryFileId: number, excluded: boolean) =>
    farmRequest<{ library_file_id: number; path: string; excluded: boolean }>(
      `/products/${projectId}/files/${libraryFileId}`,
      { method: 'PUT', body: JSON.stringify({ excluded }) },
    ),
  getProductPieces: (projectId: number) => farmRequest<FarmProductPieces>(`/products/${projectId}/pieces`),
  setProductFolder: (projectId: number, folderId: number, linked: boolean) =>
    farmRequest<{ folder_id: number; project_ids: number[] }>(`/products/${projectId}/folders/${folderId}`, {
      method: 'PUT',
      body: JSON.stringify({ linked }),
    }),
  setProductKind: (projectId: number, kind: 'set' | 'kit') =>
    farmRequest<{ project_id: number; kind: FarmProductKind }>(`/products/${projectId}/kind`, {
      method: 'PUT',
      body: JSON.stringify({ kind }),
    }),
  restoreProductPiece: (projectId: number, yieldIds: number[]) =>
    farmRequest<{ project_id: number; not_part: number }>(`/products/${projectId}/pieces/restore`, {
      method: 'PUT',
      body: JSON.stringify({ yield_ids: yieldIds }),
    }),
  setProductPiece: (projectId: number, data: FarmProductPieceInput) =>
    farmRequest<{ shape_id: number | null; component_code: string | null; quantity: number; plates_confirmed: number }>(
      `/products/${projectId}/pieces`,
      { method: 'PUT', body: JSON.stringify(data) },
    ),
  componentImageUrl: (code: string, colorHex?: string | null) =>
    // the colour in the URL only refreshes the browser's copy when the component changes colour
    withStreamToken(`/api/v1/farm/components/${code}/image${colorHex ? `?c=${colorHex}` : ''}`),
  setRecipeEntry: (projectId: number, shapeId: number, quantity: number) =>
    farmRequest<{ component_code: string; recipe: { shape_id: number; quantity: number }[] }>(
      `/products/${projectId}/recipe/${shapeId}`,
      { method: 'PUT', body: JSON.stringify({ quantity }) },
    ),
  setPart: (projectId: number, code: string, quantity: number) =>
    farmRequest<{ project_id: number; component_code: string; quantity: number }>(
      `/products/${projectId}/parts/${code}`,
      { method: 'PUT', body: JSON.stringify({ quantity }) },
    ),
  pieceImageUrl: (yieldId: number, colorHex?: string | null) =>
    withStreamToken(`/api/v1/farm/pieces/${yieldId}/image${colorHex ? `?color=${colorHex}` : ''}`),
  linkPiece: (yieldIds: number[], shapeId: number | null) =>
    farmRequest<{ linked: number; plates_confirmed: number }>('/pieces', {
      method: 'PUT',
      body: JSON.stringify({ yield_ids: yieldIds, shape_id: shapeId }),
    }),
};
