// Print farm notification events (backend/app/farm/notify.py): provider field, i18n key under `notifications.`.
export const FARM_EVENTS = [
  ['on_farm_slicing_failed', 'farmSlicingFailed'],
  ['on_farm_file_corrupt', 'farmFileCorrupt'],
  ['on_farm_plate_to_clear', 'farmPlateToClear'],
  ['on_farm_component_urgent', 'farmComponentUrgent'],
  ['on_farm_batch_to_assign', 'farmBatchToAssign'],
] as const;

export type FarmEventKey = (typeof FARM_EVENTS)[number][0];
