// Fork (printfarm): the plate's G-code read the way OrcaSlicer's GCodeProcessor reads it (libslic3r/GCode/
// GCodeProcessor.cpp, v2.5.0): one entry per move with its type, extrusion role, filament, width, height, speed,
// fan, temperature, acceleration, jerk, pressure advance and flow. The Preview draws and colours these.
//
// Plates run to hundreds of MB, so the parser streams: ``push`` takes the text a chunk at a time (no full-file
// string, no per-line arrays or objects) and the moves go into fixed-size blocks of small typed columns, copied
// once into exact-size arrays at the end (``finish``). The worker (parse.worker.ts) runs it off the page.

/** Move types, as libvgcode's EMoveType (only the ones the preview draws), and ``noop``: a jump of the position
 * (G92, after a seam marker) that is never drawn, so no segment joins the old position to the new one. */
export const MOVE = { extrude: 0, travel: 1, wipe: 2, retract: 3, unretract: 4, seam: 5, toolChange: 6, noop: 7 } as const;

/** Extrusion roles in libvgcode's EGCodeExtrusionRole order (the legend lists them in this order). */
export const ROLES = [
  'Undefined',
  'Inner wall',
  'Outer wall',
  'Overhang wall',
  'Sparse infill',
  'Internal solid infill',
  'Top surface',
  'Ironing',
  'Bridge',
  'Gap infill',
  'Skirt',
  'Support',
  'Support interface',
  'Prime tower',
  'Custom',
  'Bottom surface',
  'Internal Bridge',
  'Brim',
  'Support transition',
  'Multiple',
] as const;
export const ROLE = Object.fromEntries(ROLES.map((r, i) => [r, i])) as Record<(typeof ROLES)[number], number>;

/** Feature names of other slicers (PrusaSlicer's ";TYPE:") on OrcaSlicer's roles. */
const ROLE_ALIASES: Record<string, number> = {
  perimeter: ROLE['Inner wall'],
  'external perimeter': ROLE['Outer wall'],
  'overhang perimeter': ROLE['Overhang wall'],
  'internal infill': ROLE['Sparse infill'],
  'solid infill': ROLE['Internal solid infill'],
  'top solid infill': ROLE['Top surface'],
  'bridge infill': ROLE.Bridge,
  'internal bridge infill': ROLE['Internal Bridge'],
  'gap fill': ROLE['Gap infill'],
  'skirt/brim': ROLE.Skirt,
  'support material': ROLE.Support,
  'support material interface': ROLE['Support interface'],
  'wipe tower': ROLE['Prime tower'],
  mixed: ROLE.Multiple,
};
const ROLE_BY_NAME = new Map<string, number>(ROLES.map((r, i) => [r.toLowerCase(), i]));

export function roleOf(name: string): number {
  const n = name.trim().toLowerCase();
  return ROLE_BY_NAME.get(n) ?? ROLE_ALIASES[n] ?? ROLE.Undefined;
}

export type Box = [number, number, number, number, number, number];
const emptyBox = (): Box => [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
function grow(b: Box, x: number, y: number, z: number) {
  if (x < b[0]) b[0] = x;
  if (y < b[1]) b[1] = y;
  if (z < b[2]) b[2] = z;
  if (x > b[3]) b[3] = x;
  if (y > b[4]) b[4] = y;
  if (z > b[5]) b[5] = z;
}

/** Fixed-point scales of the stored columns: value = stored / scale. */
export const SCALE = { width: 1000, height: 1000, feedrate: 10, temperature: 10, jerk: 100, pressureAdvance: 10000, flow: 10000 } as const;
/** ``pressureAdvance`` when the G-code never set one. */
export const NO_PA = 65535;

export interface GcodeStats {
  /** Seconds: the slicer's own figures when the G-code has them. */
  totalTime: number;
  prepareTime: number;
  /** Per filament slot (index = filament - 1): length (mm), weight (g) and cost, from the G-code's footer. */
  usedMm: number[];
  usedG: number[];
  cost: number[];
  /** Volume (mm³) per filament slot: model, support, prime tower, flushed. */
  modelMm3: number[];
  supportMm3: number[];
  towerMm3: number[];
  flushMm3: number[];
  /** Volume (mm³) per role. */
  roleMm3: number[];
  filamentChanges: number;
  /** Switches between nozzles (GCodeProcessor::process_filament_change with ``filament_map``). */
  extruderChanges: number;
  /** Distance (mm; filament mm for retractions), count and seconds per move type; seconds per role. */
  typeDistance: number[];
  typeCount: number[];
  typeTime: number[];
  roleTime: number[];
  usedExtruders: number[];
  /** Extent of the extrusions (custom G-code left out) and of the prime tower: [minX, minY, minZ, maxX, maxY, maxZ],
   * null when there are none. */
  pathBox: Box | null;
  towerBox: Box | null;
  densities: number[];
  diameters: number[];
}

/** The moves, one entry per column, all of length ``count``; values in the units of ``SCALE``. */
export interface Toolpaths {
  count: number;
  /** End point of each move (x, y, z interleaved); a move starts where the one before it ends. */
  pos: Float32Array;
  type: Uint8Array;
  role: Uint8Array;
  /** Filament slot (0-based). */
  extruder: Uint8Array;
  width: Uint16Array;
  height: Uint16Array;
  feedrate: Uint16Array;
  /** Part fan, 0-255 as M106 S. */
  fan: Uint8Array;
  temperature: Uint16Array;
  /** mm/s² */
  acceleration: Uint16Array;
  jerk: Uint16Array;
  pressureAdvance: Uint16Array;
  /** Extruded volume per mm of move (mm³/mm); the volumetric flow is feedrate × this. */
  flow: Uint16Array;
  /** 1-based line in the G-code. */
  line: Uint32Array;
  /** First move of each layer, plus the move count at the end. */
  layerStart: number[];
  /** Print Z of each layer. */
  layerZ: number[];
  /** Seconds per layer (the moves before the first layer, the start G-code, are the prepare time). */
  layerTime: number[];
  stats: GcodeStats;
  /** The config block (``; key = value``). */
  config: Record<string, string>;
}

const BLOCK = 1 << 18;
const COLUMNS = {
  pos: [Float32Array, 3],
  type: [Uint8Array, 1],
  role: [Uint8Array, 1],
  extruder: [Uint8Array, 1],
  width: [Uint16Array, 1],
  height: [Uint16Array, 1],
  feedrate: [Uint16Array, 1],
  fan: [Uint8Array, 1],
  temperature: [Uint16Array, 1],
  acceleration: [Uint16Array, 1],
  jerk: [Uint16Array, 1],
  pressureAdvance: [Uint16Array, 1],
  flow: [Uint16Array, 1],
  line: [Uint32Array, 1],
} as const;
type Column = keyof typeof COLUMNS;
type Typed = Float32Array | Uint8Array | Uint16Array | Uint32Array;

/** Bytes per move of the stored columns (for the size check before parsing). */
export const BYTES_PER_MOVE = Object.values(COLUMNS).reduce((n, [T, k]) => n + T.BYTES_PER_ELEMENT * k, 0);

/** A plate too big to preview: more moves than ``limit``. */
export class TooLargeError extends Error {
  readonly moves: number;
  constructor(moves: number) {
    super(`too large: ${moves} moves`);
    this.moves = moves;
  }
}

/** "1d 2h 3m 4s", "4h 29m 38s", "10m 40s" in seconds. */
export function parseDuration(text: string): number {
  let s = 0;
  for (const m of text.matchAll(/(\d+(?:\.\d+)?)\s*([dhms])/g)) s += Number(m[1]) * { d: 86400, h: 3600, m: 60, s: 1 }[m[2] as 'd'];
  return s;
}

const numbers = (v: string | undefined): number[] =>
  (v ?? '').split(/[,;]/).map((x) => Number.parseFloat(x)).filter((x) => Number.isFinite(x));

/** Seconds to cover ``d`` mm at up to ``v`` mm/s with acceleration ``a``, starting and ending at rest. */
function trapezoid(d: number, v: number, a: number): number {
  if (v <= 0) return 0;
  if (a <= 0) return d / v;
  return d >= (v * v) / a ? d / v + v / a : 2 * Math.sqrt(d / a);
}

const clamp16 = (v: number) => (v <= 0 ? 0 : v >= 65535 ? 65535 : Math.round(v));

// G-code words: A-Z, set per line in ``has`` (bit per letter) and ``val``
const val = new Float64Array(26);

export class GcodeParser {
  /** Stop with TooLargeError past this many moves. */
  private readonly limit: number;
  constructor(limit = Infinity) {
    this.limit = limit;
  }

  private blocks: Record<Column, Typed[]> = Object.fromEntries(Object.keys(COLUMNS).map((k) => [k, []])) as unknown as Record<Column, Typed[]>;
  /** The blocks being filled. */
  private cur = {} as Record<Column, Typed>;
  private n = 0;
  private carry = '';
  private lineNo = 0;
  private config: Record<string, string> = {};
  private inConfig = false;
  private totalTime = 0;
  private modelTime = 0;
  private footer: Record<string, number[]> = {};
  private densities: number[] = [];
  private diameters: number[] = [];
  private filamentMap: number[] = [];

  private x = 0; private y = 0; private z = 0; private e = 0;
  private absolute = true; private absoluteE = true;
  private feedrate = 0;
  private role: number = ROLE.Undefined;
  private filament = -1;
  private nozzle = -1;
  private lastOnNozzle: number[] = [];
  private layer = -1;
  private fan = 0;
  private temps: number[] = [];
  private accel = 0;
  private travelAccel = 0;
  private jerk = 0;
  private pa = -1;
  private forcedWidth = 0; private forcedHeight = 0;
  private height = 0; private width = 0; private lastExtrudedZ = 0;
  private wiping = false; private flushing = false;
  private seamActive = false; private seamFirst: [number, number, number] | null = null;
  private layerStart: number[] = [];
  private layerZ: number[] = [];
  /** Raw (unscaled) seconds: [0] the prepare part (before the first layer), [1] the rest. */
  private typeRaw = [Array(8).fill(0), Array(8).fill(0)];
  private roleRaw = [Array(ROLES.length).fill(0), Array(ROLES.length).fill(0)];
  private layerRaw: number[] = [];
  private dwell = [0, 0];
  private typeDistance: number[] = Array(8).fill(0);
  private typeCount: number[] = Array(8).fill(0);
  private roleMm3: number[] = Array(ROLES.length).fill(0);
  private modelMm3: number[] = []; private supportMm3: number[] = []; private towerMm3: number[] = []; private flushMm3: number[] = [];
  private used = new Set<number>();
  private pathBox: Box = emptyBox();
  private towerBox: Box = emptyBox();
  private filamentChanges = 0;
  private extruderChanges = 0;

  /** Moves read so far. */
  get moves() {
    return this.n;
  }

  /** The next part of the G-code text (lines may be split between chunks). */
  push(chunk: string) {
    const s = this.carry + chunk;
    let start = 0;
    for (;;) {
      const nl = s.indexOf('\n', start);
      if (nl < 0) break;
      this.line(s, start, nl);
      start = nl + 1;
    }
    this.carry = s.slice(start);
  }


  private add(arr: number[], i: number, v: number) {
    arr[i] = (arr[i] ?? 0) + v;
  }

  private push1(type: number, px: number, py: number, pz: number, mm3: number) {
    if (this.n >= this.limit) throw new TooLargeError(this.n);
    const i = this.n & (BLOCK - 1);
    if (i === 0) {
      for (const c of Object.keys(COLUMNS) as Column[]) {
        const [T, k] = COLUMNS[c];
        this.cur[c] = new T(BLOCK * k);
        this.blocks[c].push(this.cur[c]);
      }
    }
    const c = this.cur;
    c.pos[3 * i] = px; c.pos[3 * i + 1] = py; c.pos[3 * i + 2] = pz;
    c.type[i] = type;
    c.role[i] = this.role;
    c.extruder[i] = Math.max(0, this.filament);
    c.width[i] = clamp16(this.width * SCALE.width);
    c.height[i] = clamp16(this.height * SCALE.height);
    c.feedrate[i] = clamp16(this.feedrate * SCALE.feedrate);
    c.fan[i] = this.fan;
    c.temperature[i] = clamp16((this.temps[Math.max(0, this.nozzle)] ?? 0) * SCALE.temperature);
    c.acceleration[i] = clamp16(type === MOVE.travel ? this.travelAccel : this.accel);
    c.jerk[i] = clamp16(this.jerk * SCALE.jerk);
    c.pressureAdvance[i] = this.pa < 0 ? NO_PA : Math.min(NO_PA - 1, clamp16(this.pa * SCALE.pressureAdvance));
    c.flow[i] = clamp16(mm3 * SCALE.flow);
    c.line[i] = this.lineNo;
    this.n++;
  }

  private radius(f: number) {
    return (this.diameters[f] ?? this.diameters[0] ?? 1.75) / 2;
  }

  /** One straight move to (nx, ny, nz) feeding dE, classified as GCodeProcessor::process_G1 does. */
  private move(nx: number, ny: number, nz: number, dE: number, arcPiece: boolean) {
    const { x, y, z } = this;
    const dx = nx - x, dy = ny - y, dz = nz - z;
    if (dx === 0 && dy === 0 && dz === 0 && dE === 0) return;
    let type: number;
    if (this.wiping) type = MOVE.wipe;
    else if (dE < 0) type = dx || dy || dz ? MOVE.travel : MOVE.retract;
    else if (dE > 0) type = dx || dy ? MOVE.extrude : dz ? MOVE.travel : MOVE.unretract;
    else type = MOVE.travel;
    const xyz = Math.sqrt(dx * dx + dy * dy + dz * dz);
    const f = Math.max(0, this.filament);
    const area = Math.PI * this.radius(f) ** 2;
    let mm3 = 0;
    if (type === MOVE.extrude) {
      const volume = area * dE;
      mm3 = volume / xyz;
      const role = this.role;
      // flushing purges (FLUSH_START..FLUSH_END) count as flushed, whatever moves they make
      if (this.flushing) this.add(this.flushMm3, f, volume);
      else if (role === ROLE.Support || role === ROLE['Support interface'] || role === ROLE['Support transition']) this.add(this.supportMm3, f, volume);
      else if (role === ROLE['Prime tower']) this.add(this.towerMm3, f, volume);
      else this.add(this.modelMm3, f, volume);
      if (!this.flushing) this.roleMm3[role] += volume;
      this.used.add(f);
      if (this.forcedHeight > 0) this.height = this.forcedHeight;
      else if (!arcPiece && nz > this.lastExtrudedZ + 1e-4) this.height = nz - this.lastExtrudedZ;
      if (this.height === 0) this.height = 0.2;
      if (!arcPiece) this.lastExtrudedZ = nz;
      const r = this.radius(f);
      const h = this.height;
      if (this.forcedWidth > 0) this.width = this.forcedWidth;
      else if (role === ROLE['Outer wall']) this.width = (dE * Math.PI * (1.05 * r) ** 2) / (xyz * h);
      else if (role === ROLE.Bridge || role === ROLE['Internal Bridge'] || role === ROLE.Undefined) this.width = 2 * r * Math.sqrt(dE / xyz);
      else this.width = (dE * Math.PI * r * r) / (xyz * h) + (1 - 0.25 * Math.PI) * h;
      if (this.width === 0) this.width = 0.4;
      this.width = Math.min(this.width, Math.max(2, 4 * h));
      // the paths the bed checks look at (BuildVolume::all_paths_inside: extrusions, not custom G-code)
      if (role !== ROLE.Custom && this.width > 0 && h > 0) {
        grow(this.pathBox, nx, ny, nz);
        if (role === ROLE['Prime tower']) grow(this.towerBox, nx, ny, nz);
      }
    } else if (type === MOVE.unretract && this.flushing) {
      this.add(this.flushMm3, f, area * dE);
    }
    this.typeDistance[type] += type === MOVE.retract || type === MOVE.unretract ? Math.abs(dE) : xyz;
    this.typeCount[type]++;
    // Seams (GCodeProcessor::process_G1): an outer wall loop that ends within 0.25 mm of where it began
    if (this.seamActive) {
      if (type === MOVE.extrude && this.role === ROLE['Outer wall']) {
        this.seamFirst ??= [x, y, z];
      } else if (this.seamFirst && (type !== MOVE.extrude || (this.role !== ROLE['Outer wall'] && this.role !== ROLE['Overhang wall']))) {
        const [fx, fy, fz] = this.seamFirst;
        if ((x - fx) ** 2 + (y - fy) ** 2 + (z - fz) ** 2 < 0.0625) {
          this.push1(MOVE.seam, (x + fx) / 2, (y + fy) / 2, (z + fz) / 2, 0);
          this.push1(MOVE.noop, x, y, z, 0); // the next move starts at the nozzle, not at the marker
          this.typeCount[MOVE.seam]++;
        }
        this.seamActive = false;
        this.seamFirst = null;
      }
    } else if (type === MOVE.extrude && this.role === ROLE['Outer wall']) {
      this.seamActive = true;
      this.seamFirst = [x, y, z];
    }
    const section = this.layer < 0 ? 0 : 1;
    const t = trapezoid(xyz || Math.abs(dE), this.feedrate, type === MOVE.travel ? this.travelAccel : this.accel);
    this.typeRaw[section][type] += t;
    if (type === MOVE.extrude) this.roleRaw[section][this.role] += t;
    if (section) this.layerRaw[this.layer] += t;
    this.push1(type, nx, ny, nz, mm3);
    this.x = nx; this.y = ny; this.z = nz;
  }

  /** GCodeProcessor::process_filament_change: a change of filament, of nozzle, or both (``filament_map``). */
  private tool(next: number) {
    if (next === this.filament) return;
    const prevNozzle = this.nozzle;
    const nextNozzle = Math.max(0, (this.filamentMap[next] ?? 1) - 1);
    if (prevNozzle === nextNozzle) this.filamentChanges++;
    else if (prevNozzle >= 0) {
      const last = this.lastOnNozzle[nextNozzle] ?? -1;
      if (last >= 0 && last !== next) this.filamentChanges++;
      this.extruderChanges++;
    } // else: the first filament loaded, not a change
    if (prevNozzle >= 0) this.lastOnNozzle[prevNozzle] = this.filament;
    this.lastOnNozzle[nextNozzle] = next;
    this.nozzle = nextNozzle;
    this.filament = next;
    this.push1(MOVE.toolChange, this.x, this.y, this.z, 0);
    this.typeCount[MOVE.toolChange]++;
  }

  private comment(s: string, a: number, b: number) {
    // a: just after ';'
    while (a < b && s.charCodeAt(a) === 32) a++;
    const at = (w: string) => s.startsWith(w, a);
    const rest = () => s.slice(s.indexOf(':', a) + 1, b);
    const num = () => Number.parseFloat(rest());
    if (at('FEATURE:') || at('TYPE:')) this.role = roleOf(rest());
    else if (at('CHANGE_LAYER') || at('LAYER_CHANGE')) {
      this.layer++;
      this.layerStart[this.layer] = this.n;
      this.layerZ[this.layer] = this.layerZ[this.layer - 1] ?? 0;
      this.layerRaw[this.layer] = 0;
    } else if (at('Z_HEIGHT:') || at('Z:')) {
      if (this.layer >= 0) this.layerZ[this.layer] = num();
    } else if (at('LAYER_HEIGHT:') || at('HEIGHT:')) this.forcedHeight = num() || 0;
    else if (at('LINE_WIDTH:') || at('WIDTH:')) this.forcedWidth = num() || 0;
    else if (at('WIPE_START')) this.wiping = true;
    else if (at('WIPE_END')) this.wiping = false;
    else if (at('FLUSH_START')) this.flushing = true;
    else if (at('FLUSH_END')) this.flushing = false;
    else if (at('CONFIG_BLOCK_START')) this.inConfig = true;
    else if (at('CONFIG_BLOCK_END')) this.inConfig = false;
    else if (this.inConfig) {
      const eq = s.indexOf(' = ', a);
      if (eq > 0 && eq < b) this.setConfig(s.slice(a, eq), s.slice(eq + 3, b).trimEnd());
    } else if (at('model printing time:')) {
      const t = s.slice(a, b);
      const [model, total] = t.split(';');
      this.modelTime = parseDuration(model.slice(model.indexOf(':') + 1));
      if (total) this.totalTime = parseDuration(total.slice(total.indexOf(':') + 1));
    } else if (at('estimated printing time')) {
      this.totalTime ||= parseDuration(s.slice(s.indexOf('=', a) + 1, b));
    } else if (at('filament used [') || at('filament cost')) {
      const eq = s.indexOf('=', a);
      if (eq > 0 && eq < b) this.footer[s.slice(a, eq).trim()] = numbers(s.slice(eq + 1, b));
    } else if (at('filament_density:')) this.setConfig('filament_density', rest().trim(), true);
    else if (at('filament_diameter:')) this.setConfig('filament_diameter', rest().trim(), true);
  }

  private setConfig(key: string, value: string, header = false) {
    if (header && key in this.config) return;
    this.config[key] = value;
    const first = (v: string) => numbers(v)[0] ?? 0;
    if (key === 'filament_density') this.densities = numbers(value);
    else if (key === 'filament_diameter') this.diameters = numbers(value);
    else if (key === 'filament_map') this.filamentMap = numbers(value);
    else if (key === 'default_acceleration' && first(value)) this.accel ||= first(value);
    else if (key === 'travel_acceleration' && first(value)) this.travelAccel ||= first(value);
    else if (key === 'machine_max_jerk_x') this.jerk ||= first(value);
    else if (key === 'pressure_advance' && this.pa < 0) this.pa = first(value);
  }

  private line(s: string, a: number, b: number) {
    this.lineNo++;
    if (b > a && s.charCodeAt(b - 1) === 13) b--; // \r\n
    while (a < b && (s.charCodeAt(a) === 32 || s.charCodeAt(a) === 9)) a++;
    if (a >= b) return;
    const c0 = s.charCodeAt(a);
    if (c0 === 59) {
      this.comment(s, a + 1, b);
      return;
    }
    const semi = s.indexOf(';', a);
    const end = semi >= 0 && semi < b ? semi : b;
    // the command: a letter and its number (G1, M204, T1, T1A) or a Klipper macro (SET_VELOCITY_LIMIT ...)
    const letter = c0 & ~32;
    let i = a + 1;
    let code = 0;
    let sub = 0;
    while (i < end && s.charCodeAt(i) >= 48 && s.charCodeAt(i) <= 57) code = code * 10 + s.charCodeAt(i++) - 48;
    if (i < end && s.charCodeAt(i) === 46) {
      i++; // M620.11
      while (i < end && s.charCodeAt(i) >= 48 && s.charCodeAt(i) <= 57) sub = sub * 10 + s.charCodeAt(i++) - 48;
      sub = sub || -1;
    }
    if (i === a + 1) {
      if (letter === 83) this.macro(s.slice(a, end)); // 'S'
      return;
    }
    if (letter === 84) {
      // T<n>, also Creality's T1A (the letter after the number is the CFS slot; the filament is the number)
      if (code < 255) this.tool(code);
      return;
    }
    if (sub) return;
    let has = 0;
    while (i < end) {
      const c = s.charCodeAt(i);
      const u = c & ~32;
      if (u >= 65 && u <= 90 && c !== 32) {
        // a word: letter + number
        let j = i + 1;
        let neg = false;
        if (s.charCodeAt(j) === 45) {
          neg = true;
          j++;
        } else if (s.charCodeAt(j) === 43) j++;
        let v = 0;
        let digits = 0;
        while (j < end) {
          const d = s.charCodeAt(j) - 48;
          if (d < 0 || d > 9) break;
          v = v * 10 + d;
          j++;
          digits++;
        }
        if (s.charCodeAt(j) === 46) {
          j++;
          let scale = 0.1;
          while (j < end) {
            const d = s.charCodeAt(j) - 48;
            if (d < 0 || d > 9) break;
            v += d * scale;
            scale *= 0.1;
            j++;
            digits++;
          }
        }
        if (digits) {
          has |= 1 << (u - 65);
          val[u - 65] = neg ? -v : v;
        }
        i = j;
      } else i++;
    }
    const H = (l: string) => (has >> (l.charCodeAt(0) - 65)) & 1;
    const V = (l: string) => val[l.charCodeAt(0) - 65];
    if (letter === 71) this.g(code, has, H, V);
    else if (letter === 77) this.m(code, H, V);
  }

  private g(code: number, has: number, H: (l: string) => number, V: (l: string) => number) {
    if (code <= 3) {
      if (H('F')) this.feedrate = V('F') / 60;
      const nx = H('X') ? (this.absolute ? V('X') : this.x + V('X')) : this.x;
      const ny = H('Y') ? (this.absolute ? V('Y') : this.y + V('Y')) : this.y;
      const nz = H('Z') ? (this.absolute ? V('Z') : this.z + V('Z')) : this.z;
      let dE = 0;
      if (H('E')) {
        dE = this.absoluteE ? V('E') - this.e : V('E');
        this.e = this.absoluteE ? V('E') : this.e + V('E');
      }
      if (code <= 1) {
        this.move(nx, ny, nz, dE, false);
        return;
      }
      // G2 / G3: chords of about 1 mm, as GCodeProcessor discretizes arcs
      const cx = this.x + (H('I') ? V('I') : 0), cy = this.y + (H('J') ? V('J') : 0);
      const r = Math.hypot(this.x - cx, this.y - cy);
      let angle = Math.atan2(this.y - cy, this.x - cx);
      let sweep = Math.atan2(ny - cy, nx - cx) - angle;
      if (code === 2 && sweep >= 0) sweep -= 2 * Math.PI;
      if (code === 3 && sweep <= 0) sweep += 2 * Math.PI;
      const steps = r > 0 ? Math.min(64, Math.max(1, Math.ceil(Math.abs(sweep) * r))) : 1;
      const z0 = this.z;
      for (let k = 1; k <= steps; k++) {
        const last = k === steps;
        angle += sweep / steps;
        this.move(last ? nx : cx + r * Math.cos(angle), last ? ny : cy + r * Math.sin(angle), z0 + ((nz - z0) * k) / steps, dE / steps, !last);
      }
    } else if (code === 4) {
      this.dwell[this.layer < 0 ? 0 : 1] += H('S') ? V('S') : (H('P') ? V('P') : 0) / 1000;
    } else if (code === 90) this.absolute = true; // only the axes: the extruder follows M82 / M83 (Orca, Klipper)
    else if (code === 91) this.absolute = false;
    else if (code === 92) {
      if (H('E')) this.e = V('E');
      if (has & ((1 << 23) | (1 << 24) | (1 << 25))) {
        if (H('X')) this.x = V('X');
        if (H('Y')) this.y = V('Y');
        if (H('Z')) this.z = V('Z');
        this.push1(MOVE.noop, this.x, this.y, this.z, 0); // the path restarts here: no segment to it
      }
    }
  }

  private m(code: number, H: (l: string) => number, V: (l: string) => number) {
    switch (code) {
      case 82:
        this.absoluteE = true;
        break;
      case 83:
        this.absoluteE = false;
        break;
      case 106: // Bambu printers use both M106 and M106 P1 for the part fan (process_M106)
        if (!H('P') || V('P') === 1) this.fan = H('S') ? Math.max(0, Math.min(255, Math.round(V('S')))) : 255;
        break;
      case 107:
        if (!H('P') || V('P') === 1) this.fan = 0;
        break;
      case 104:
      case 109:
        if (H('S')) this.temps[H('T') ? V('T') : Math.max(0, this.nozzle)] = V('S');
        break;
      case 204:
        if (H('S')) this.accel = this.travelAccel = V('S');
        if (H('P')) this.accel = V('P');
        if (H('T')) this.travelAccel = V('T');
        break;
      case 205:
        if (H('X')) this.jerk = V('X');
        break;
      case 900:
        if (H('K')) this.pa = Math.max(0, V('K'));
        break;
    }
  }

  private macro(text: string) {
    const a = text.match(/ACCEL=([\d.]+)/i);
    if (/^SET_VELOCITY_LIMIT/i.test(text)) {
      const scv = text.match(/SQUARE_CORNER_VELOCITY=([\d.]+)/i);
      if (a) this.accel = this.travelAccel = Number(a[1]);
      if (scv) this.jerk = Number(scv[1]);
    } else if (/^SET_PRESSURE_ADVANCE/i.test(text)) {
      const adv = text.match(/ADVANCE=([\d.]+)/i);
      if (adv) this.pa = Number(adv[1]);
    }
  }

  /** The moves in exact-size columns (each block freed as it is copied) and the statistics. */
  finish(): Toolpaths {
    if (this.carry) {
      this.line(this.carry, 0, this.carry.length);
      this.carry = '';
    }
    const count = this.n;
    const out = {} as Record<Column, Typed>;
    for (const c of Object.keys(COLUMNS) as Column[]) {
      const [T, k] = COLUMNS[c];
      const col = new T(count * k);
      this.blocks[c].forEach((b, at) => {
        const from = at * BLOCK;
        const len = Math.min(BLOCK, count - from);
        if (len > 0) col.set(b.subarray(0, len * k), from * k);
      });
      this.blocks[c] = [];
      out[c] = col;
    }

    // Times: the moves' stop-and-go estimate scaled to the slicer's figures, the start G-code (before the first
    // layer) to the prepare time and the rest to the model printing time.
    // shortcut: per-role, per-option and per-layer times are proportional to the slicer's planner, not its
    // exact figures; port GCodeProcessor's time planner for those.
    const total = this.totalTime;
    const prepare = this.modelTime && total > this.modelTime ? total - this.modelTime : 0;
    const raw = [0, 1].map((k) => this.typeRaw[k].reduce((a, b) => a + b, 0));
    const scale = (k: number, target: number) => (target > this.dwell[k] && raw[k] > 0 ? (target - this.dwell[k]) / raw[k] : 1);
    const k0 = this.modelTime ? scale(0, prepare) : total ? scale(0, total * (raw[0] / (raw[0] + raw[1] || 1))) : 1;
    const k1 = this.modelTime ? scale(1, this.modelTime) : total ? scale(1, total * (raw[1] / (raw[0] + raw[1] || 1))) : 1;
    const typeTime = this.typeRaw[0].map((t, i) => t * k0 + this.typeRaw[1][i] * k1);
    const roleTime = this.roleRaw[0].map((t, i) => t * k0 + this.roleRaw[1][i] * k1);
    const totalTime = total || raw[0] + raw[1] + this.dwell[0] + this.dwell[1];

    const layerStart = this.layerStart.length ? [...this.layerStart] : [0];
    const layerZ = this.layerZ.length ? [...this.layerZ] : [0];
    layerStart[0] = 0; // the start G-code belongs to the first layer
    layerStart.push(count);
    const layerTime = layerStart.slice(0, -1).map((_, l) => (this.layerRaw[l] ?? 0) * k1);
    const pos = out.pos as Float32Array, type = out.type as Uint8Array;
    for (let l = 0; l < layerZ.length; l++) {
      for (let i = layerStart[l]; !layerZ[l] && i < layerStart[l + 1]; i++) if (type[i] === MOVE.extrude) layerZ[l] = pos[3 * i + 2];
    }

    const area = (s: number) => Math.PI * this.radius(s) ** 2;
    const slots = Math.max(this.densities.length, this.used.size ? Math.max(...this.used) + 1 : 0);
    const usedMm = this.footer['filament used [mm]'] ?? [];
    const usedG = this.footer['filament used [g]'] ?? [];
    const cost = this.footer['filament cost'] ?? [];
    const costPerKg = numbers(this.config.filament_cost);
    for (let s = 0; s < slots; s++) {
      const mm3 = (this.modelMm3[s] ?? 0) + (this.supportMm3[s] ?? 0) + (this.towerMm3[s] ?? 0) + (this.flushMm3[s] ?? 0);
      usedMm[s] ??= mm3 / area(s);
      usedG[s] ??= (mm3 / 1000) * (this.densities[s] ?? 1.24);
      cost[s] ??= (usedG[s] / 1000) * (costPerKg[s] ?? 0);
    }

    return {
      count,
      pos,
      type,
      role: out.role as Uint8Array,
      extruder: out.extruder as Uint8Array,
      width: out.width as Uint16Array,
      height: out.height as Uint16Array,
      feedrate: out.feedrate as Uint16Array,
      fan: out.fan as Uint8Array,
      temperature: out.temperature as Uint16Array,
      acceleration: out.acceleration as Uint16Array,
      jerk: out.jerk as Uint16Array,
      pressureAdvance: out.pressureAdvance as Uint16Array,
      flow: out.flow as Uint16Array,
      line: out.line as Uint32Array,
      layerStart,
      layerZ,
      layerTime,
      config: this.config,
      stats: {
        totalTime,
        prepareTime: prepare,
        usedMm, usedG, cost,
        modelMm3: this.modelMm3, supportMm3: this.supportMm3, towerMm3: this.towerMm3, flushMm3: this.flushMm3,
        roleMm3: this.roleMm3,
        filamentChanges: this.filamentChanges,
        extruderChanges: this.extruderChanges,
        typeDistance: this.typeDistance, typeCount: this.typeCount, typeTime, roleTime,
        usedExtruders: [...this.used].sort((a, b) => a - b),
        pathBox: Number.isFinite(this.pathBox[0]) ? this.pathBox : null,
        towerBox: Number.isFinite(this.towerBox[0]) ? this.towerBox : null,
        densities: this.densities, diameters: this.diameters,
      },
    };
  }
}

/** The whole G-code at once (tests, small files). */
export function parseGcode(text: string): Toolpaths {
  const p = new GcodeParser();
  p.push(text);
  return p.finish();
}

/** The transferable buffers of a Toolpaths (the worker hands them over without copying). */
export function buffers(tp: Toolpaths): ArrayBuffer[] {
  return (Object.keys(COLUMNS) as Column[]).map((c) => (tp[c] as Typed).buffer as ArrayBuffer);
}

/** The layer of move ``i`` (binary search in ``layerStart``). */
export function layerOf(tp: Toolpaths, i: number): number {
  let lo = 0, hi = tp.layerStart.length - 2;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (tp.layerStart[mid] <= i) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}
