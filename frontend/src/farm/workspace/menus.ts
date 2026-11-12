// Fork (printfarm): OrcaSlicer's right-click menus of an object and of a plate (GUI_Factories.cpp, v2.5.0, in its
// order), the same in the objects tree and in the 3D view. Items with an action work now (they only look);
// the others are shown disabled until phase 2 can write them into the 3MF.
import type { MenuItem } from './Viewport';

type Tr = (text: string) => string;

/** Align/Distribute: its submenu (align.ts), or greyed with why (a string), or (none given) phase 2. */
const alignItem = (tr: Tr, align?: MenuItem[] | string): MenuItem =>
  Array.isArray(align) ? { label: tr('Align/Distribute'), items: align } : { label: tr('Align/Distribute'), submenu: true, hint: align };

export function objectMenu(
  tr: Tr,
  deleteKey: string,
  printable: boolean,
  on: { select: () => void; zoom: () => void; info: () => void; settings: () => void; table?: () => void },
  align?: MenuItem[] | string,
): MenuItem[] {
  return [
    { label: tr('Select'), onClick: on.select },
    { label: tr('Zoom'), shortcut: 'Z', onClick: on.zoom },
    { label: tr('Info'), onClick: on.info },
    { label: tr('Clone'), shortcut: 'Ctrl+K', separator: true },
    { label: tr('Instances'), submenu: true },
    { label: tr('Center'), separator: true },
    { label: tr('Drop') },
    { label: tr('Transform'), submenu: true },
    alignItem(tr, align),
    { label: tr('Edit'), submenu: true },
    { label: tr('Delete'), shortcut: deleteKey, separator: true },
    { label: tr('Add Part'), icon: 'menu_add_part', submenu: true, separator: true },
    { label: tr('Add Negative Part'), icon: 'menu_add_negative', submenu: true },
    { label: tr('Add Modifier'), icon: 'menu_add_modifier', submenu: true },
    { label: tr('Add Support Blocker'), icon: 'menu_support_blocker', submenu: true },
    { label: tr('Add Support Enforcer'), icon: 'menu_support_enforcer', submenu: true },
    { label: tr('Add Precise Seam'), submenu: true },
    { label: tr('Height Range Modifier') },
    { label: tr('Printable'), shortcut: 'V', checked: printable, separator: true },
    { label: tr('Auto Drop'), checked: true },
    { label: tr('Edit Process Settings'), onClick: on.settings, separator: true },
    { label: tr('Copy Process Settings') },
    { label: tr('Paste Process Settings') },
    { label: tr('Edit in Parameter Table'), onClick: on.table },
    { label: tr('More'), submenu: true, separator: true },
  ];
}

export function plateMenu(tr: Tr, on: { selectAll: () => void; settings: () => void }): MenuItem[] {
  return [
    { label: tr('Select All'), onClick: on.selectAll },
    { label: tr('Select All Plates') },
    { label: tr('Plate Settings'), onClick: on.settings },
    { label: tr('Delete All'), separator: true },
    { label: tr('Arrange'), separator: true },
    { label: tr('Auto Rotate') },
    { label: tr('Order By'), submenu: true },
    { label: tr('Edit'), submenu: true },
    { label: tr('Edit Plate Name'), separator: true },
    { label: tr('Lock') },
    { label: tr('Delete Plate'), separator: true },
    { label: tr('Add Primitive'), icon: 'menu_add_part', submenu: true, separator: true },
    { label: tr('Add Handy models'), icon: 'menu_add_part', submenu: true },
    { label: tr('Add Models'), icon: 'menu_add_part' },
    { label: tr('Reload All'), separator: true },
    { label: `${tr('Replace all with 3D files')}...`, separator: true },
  ];
}

/** A part, negative part, modifier, blocker or enforcer (GUI_Factories.cpp part menu). */
export function partMenu(
  tr: Tr,
  deleteKey: string,
  on: { select: () => void; zoom: () => void; settings: () => void; table?: () => void },
): MenuItem[] {
  return [
    { label: tr('Select'), onClick: on.select },
    { label: tr('Zoom'), shortcut: 'Z', onClick: on.zoom },
    { label: tr('Center'), separator: true },
    { label: tr('Drop') },
    { label: tr('Transform'), submenu: true },
    { label: tr('Align/Distribute'), submenu: true },
    { label: tr('Edit'), submenu: true },
    { label: tr('Delete'), shortcut: deleteKey, separator: true },
    { label: tr('Change Type'), submenu: true, separator: true },
    { label: tr('Precise Seam Type'), submenu: true },
    { label: tr('Change Filament'), submenu: true },
    { label: tr('Edit Process Settings'), onClick: on.settings, separator: true },
    { label: tr('Copy Process Settings') },
    { label: tr('Paste Process Settings') },
    { label: tr('Edit in Parameter Table'), onClick: on.table },
    { label: tr('More'), submenu: true, separator: true },
  ];
}

/** Several objects selected (GUI_Factories.cpp multi selection menu). */
export function multiMenu(
  tr: Tr,
  deleteKey: string,
  printable: boolean,
  on: { zoom: () => void; deselect: () => void },
  align?: MenuItem[] | string,
): MenuItem[] {
  return [
    { label: tr('Zoom'), shortcut: 'Z', onClick: on.zoom },
    { label: tr('Deselect All'), shortcut: 'Esc', onClick: on.deselect },
    { label: tr('Assemble'), separator: true },
    { label: tr('Center') },
    { label: tr('Drop') },
    { label: tr('Transform'), submenu: true },
    alignItem(tr, align),
    { label: tr('Fix Model') },
    { label: tr('Delete'), shortcut: deleteKey, separator: true },
    { label: tr('Set Filament for selected items'), submenu: true, separator: true },
    { label: tr('Printable'), shortcut: 'V', checked: printable },
    { label: tr('Auto Drop'), checked: true },
    { label: tr('Edit Process Settings'), separator: true },
    { label: tr('Copy Process Settings') },
    { label: tr('Paste Process Settings') },
    { label: tr('More'), submenu: true, separator: true },
  ];
}

/** One instance, or several of the same object, in the list (GUI_Factories.cpp instance menu): its only item. */
export function instanceMenu(tr: Tr, count: number): MenuItem[] {
  return [{ label: tr(count > 1 ? 'Set as Individual Objects' : 'Set as An Individual Object') }];
}

/** The empty 3D view or list (GUI_Factories.cpp default menu). */
export function emptyMenu(tr: Tr, labels: boolean, on: { labels: () => void }): MenuItem[] {
  return [
    { label: tr('Add Primitive'), icon: 'menu_add_part', submenu: true },
    { label: tr('Add Handy models'), icon: 'menu_add_part', submenu: true },
    { label: tr('Add Models'), icon: 'menu_add_part' },
    { label: tr('Show Labels'), shortcut: 'Ctrl+E', checked: labels, onClick: on.labels, separator: true },
  ];
}
