// Fork (printfarm): the plate's G-code read the way OrcaSlicer's GCodeProcessor reads it (libslic3r/GCode/
// GCodeProcessor.cpp, v2.5.0): one entry per move with its type, extrusion role, layer, width, height, speed,
// fan, temperature, acceleration, jerk, pressure advance, filament and time. The Preview draws and colours these.

/** Move types, as libvgcode's EMoveType (only the ones the preview draws). */
export const MOVE = { extrude: 0, travel: 1, wipe: 2, retract: 3, unretract: 4, seam: 5, toolChange: 6 } as const;

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

export interface GcodeStats {
  /** Seconds; the slicer's own figures when the G-code has them. */
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
  extruderChanges: number;
  /** Distance (mm; filament mm for retractions), count and seconds per move type; seconds per role. */
  typeDistance: number[];
  typeCount: number[];
  typeTime: number[];
  roleTime: number[];
  usedExtruders: number[];
  densities: number[];
  diameters: number[];
}

export interface Toolpaths {
  count: number;
  /** End point of each move; a move starts where the one before it ends. */
  x: Float32Array;
  y: Float32Array;
  z: Float32Array;
  type: Uint8Array;
  role: Uint8Array;
  extruder: Uint8Array;
  layer: Uint32Array;
  width: Float32Array;
  height: Float32Array;
  /** mm/s */
  feedrate: Float32Array;
  /** % */
  fan: Float32Array;
  temperature: Float32Array;
  acceleration: Float32Array;
  jerk: Float32Array;
  pressureAdvance: Float32Array;
  /** Extruded volume per mm of move (mm³/mm); the volumetric flow is feedrate × this. */
  mm3PerMm: Float32Array;
  /** Filament fed by the move, in mm (negative for retractions). */
  e: Float32Array;
  /** Estimated seconds, scaled so the moves add up to the slicer's own estimate. */
  time: Float32Array;
  /** 0-based line in the G-code. */
  line: Uint32Array;
  /** First move of each layer, plus the move count at the end. */
  layerStart: number[];
  /** Print Z of each layer. */
  layerZ: number[];
  layerTime: number[];
  stats: GcodeStats;
  /** The config block (``; key = value``). */
  config: Record<string, string>;
}

const F32 = ['x', 'y', 'z', 'width', 'height', 'feedrate', 'fan', 'temperature', 'acceleration', 'jerk', 'pressureAdvance', 'mm3PerMm', 'e', 'time'] as const;
type F32Key = (typeof F32)[number];

/** Growable typed columns, one entry per move. */
class Columns {
  n = 0;
  private cap = 1 << 14;
  f = Object.fromEntries(F32.map((k) => [k, new Float32Array(1 << 14)])) as Record<F32Key, Float32Array>;
  type = new Uint8Array(this.cap);
  role = new Uint8Array(this.cap);
  extruder = new Uint8Array(this.cap);
  layer = new Uint32Array(this.cap);
  line = new Uint32Array(this.cap);

  next(): number {
    if (this.n === this.cap) {
      this.cap *= 2;
      const grow = <T extends Float32Array | Uint8Array | Uint32Array>(a: T, make: (n: number) => T): T => {
        const b = make(this.cap);
        b.set(a);
        return b;
      };
      for (const k of F32) this.f[k] = grow(this.f[k], (n) => new Float32Array(n));
      this.type = grow(this.type, (n) => new Uint8Array(n));
      this.role = grow(this.role, (n) => new Uint8Array(n));
      this.extruder = grow(this.extruder, (n) => new Uint8Array(n));
      this.layer = grow(this.layer, (n) => new Uint32Array(n));
      this.line = grow(this.line, (n) => new Uint32Array(n));
    }
    return this.n++;
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

const tagValue = (tag: string) => Number.parseFloat(tag.slice(tag.indexOf(':') + 1));

export function parseGcode(text: string): Toolpaths {
  const c = new Columns();
  const config: Record<string, string> = {};
  const lines = text.split('\n');

  // Header, footer and config block first: densities, diameters, accelerations and the slicer's totals.
  let inConfig = false;
  let totalTime = 0;
  let modelTime = 0;
  const footer: Record<string, number[]> = {};
  for (const raw of lines) {
    if (raw.charCodeAt(0) !== 59) continue; // ';'
    const t = raw.slice(1).trim();
    if (t === 'CONFIG_BLOCK_START') inConfig = true;
    else if (t === 'CONFIG_BLOCK_END') inConfig = false;
    else if (inConfig) {
      const eq = t.indexOf(' = ');
      if (eq > 0) config[t.slice(0, eq)] = t.slice(eq + 3);
    } else if (t.startsWith('model printing time:')) {
      const [model, total] = t.split(';');
      modelTime = parseDuration(model.slice(model.indexOf(':') + 1));
      if (total) totalTime = parseDuration(total.slice(total.indexOf(':') + 1));
    } else if (t.startsWith('estimated printing time')) {
      totalTime ||= parseDuration(t.slice(t.indexOf('=') + 1));
    } else if (t.startsWith('filament used [') || t.startsWith('filament cost')) {
      const eq = t.indexOf('=');
      if (eq > 0) footer[t.slice(0, eq).trim()] = numbers(t.slice(eq + 1));
    } else if (t.startsWith('filament_density:')) config.filament_density ??= t.slice(17).trim();
    else if (t.startsWith('filament_diameter:')) config.filament_diameter ??= t.slice(18).trim();
  }
  const densities = numbers(config.filament_density);
  const diameters = numbers(config.filament_diameter);
  const radius = (ext: number) => (diameters[ext] ?? diameters[0] ?? 1.75) / 2;
  const area = (ext: number) => Math.PI * radius(ext) ** 2;
  const first = (key: string) => numbers(config[key])[0] ?? 0;

  let x = 0, y = 0, z = 0, e = 0;
  let absolute = true, absoluteE = true;
  let feedrate = 0;
  let role: number = ROLE.Undefined;
  let extruder = 0;
  let layer = -1;
  let fan = 0;
  const temps: number[] = [];
  let accel = first('default_acceleration') || first('machine_max_acceleration_extruding') || 1000;
  let travelAccel = first('travel_acceleration') || accel;
  let jerk = first('machine_max_jerk_x') || first('default_jerk');
  let pa = first('pressure_advance');
  let forcedWidth = 0, forcedHeight = 0;
  let height = 0, width = 0, lastExtrudedZ = 0;
  let wiping = false, flushing = false;
  let seamFirst: [number, number, number] | null = null;
  let seamActive = false;
  let dwell = 0;
  const layerStart: number[] = [];
  const layerZ: number[] = [];
  const typeDistance: number[] = Array(7).fill(0);
  const typeCount: number[] = Array(7).fill(0);
  const roleMm3: number[] = Array(ROLES.length).fill(0);
  const modelMm3: number[] = [], supportMm3: number[] = [], towerMm3: number[] = [], flushMm3: number[] = [];
  const used = new Set<number>();
  let filamentChanges = 0;
  const add = (arr: number[], i: number, v: number) => (arr[i] = (arr[i] ?? 0) + v);

  const push = (type: number, px: number, py: number, pz: number, lineNo: number, dE: number, mm3: number, time: number) => {
    const i = c.next();
    const f = c.f;
    f.x[i] = px; f.y[i] = py; f.z[i] = pz;
    c.type[i] = type; c.role[i] = role; c.extruder[i] = extruder;
    c.layer[i] = Math.max(0, layer); c.line[i] = lineNo;
    f.width[i] = width; f.height[i] = height; f.feedrate[i] = feedrate; f.fan[i] = fan;
    f.temperature[i] = temps[extruder] ?? 0;
    f.acceleration[i] = type === MOVE.travel ? travelAccel : accel;
    f.jerk[i] = jerk; f.pressureAdvance[i] = pa; f.mm3PerMm[i] = mm3; f.e[i] = dE; f.time[i] = time;
  };

  /** One straight move to (nx, ny, nz) feeding dE, classified as GCodeProcessor::process_G1 does. */
  const move = (nx: number, ny: number, nz: number, dE: number, lineNo: number, arcPiece: boolean) => {
    const dx = nx - x, dy = ny - y, dz = nz - z;
    if (dx === 0 && dy === 0 && dz === 0 && dE === 0) return;
    let type: number;
    if (wiping) type = MOVE.wipe;
    else if (dE < 0) type = dx || dy || dz ? MOVE.travel : MOVE.retract;
    else if (dE > 0) type = dx || dy ? MOVE.extrude : dz ? MOVE.travel : MOVE.unretract;
    else type = MOVE.travel;
    const xyz = Math.sqrt(dx * dx + dy * dy + dz * dz);
    let mm3 = 0;
    if (type === MOVE.extrude) {
      const volume = area(extruder) * dE;
      mm3 = volume / xyz;
      if (role === ROLE.Support || role === ROLE['Support interface'] || role === ROLE['Support transition']) add(supportMm3, extruder, volume);
      else if (role === ROLE['Prime tower']) add(towerMm3, extruder, volume);
      else add(modelMm3, extruder, volume);
      roleMm3[role] += volume;
      used.add(extruder);
      if (forcedHeight > 0) height = forcedHeight;
      else if (!arcPiece && nz > lastExtrudedZ + 1e-4) height = nz - lastExtrudedZ;
      if (height === 0) height = 0.2;
      if (!arcPiece) lastExtrudedZ = nz;
      const r = radius(extruder);
      if (forcedWidth > 0) width = forcedWidth;
      else if (role === ROLE['Outer wall']) width = (dE * Math.PI * (1.05 * r) ** 2) / (xyz * height);
      else if (role === ROLE.Bridge || role === ROLE['Internal Bridge'] || role === ROLE.Undefined) width = 2 * r * Math.sqrt(dE / xyz);
      else width = (dE * Math.PI * r * r) / (xyz * height) + (1 - 0.25 * Math.PI) * height;
      if (width === 0) width = 0.4;
      width = Math.min(width, Math.max(2, 4 * height));
    } else if (type === MOVE.unretract && flushing) {
      add(flushMm3, extruder, area(extruder) * dE);
    }
    typeDistance[type] += type === MOVE.retract || type === MOVE.unretract ? Math.abs(dE) : xyz;
    typeCount[type]++;
    // Seams (GCodeProcessor::process_G1): an outer wall loop that ends within 0.25 mm of where it began.
    if (seamActive) {
      if (type === MOVE.extrude && role === ROLE['Outer wall']) {
        seamFirst ??= [x, y, z];
      } else if (seamFirst && (type !== MOVE.extrude || (role !== ROLE['Outer wall'] && role !== ROLE['Overhang wall']))) {
        const [fx, fy, fz] = seamFirst;
        if ((x - fx) ** 2 + (y - fy) ** 2 + (z - fz) ** 2 < 0.0625) {
          push(MOVE.seam, (x + fx) / 2, (y + fy) / 2, (z + fz) / 2, lineNo, 0, 0, 0);
          typeCount[MOVE.seam]++;
        }
        seamActive = false;
        seamFirst = null;
      }
    } else if (type === MOVE.extrude && role === ROLE['Outer wall']) {
      seamActive = true;
      seamFirst = [x, y, z];
    }
    push(type, nx, ny, nz, lineNo, dE, mm3, trapezoid(xyz || Math.abs(dE), feedrate, type === MOVE.travel ? travelAccel : accel));
    x = nx; y = ny; z = nz;
  };

  for (let lineNo = 0; lineNo < lines.length; lineNo++) {
    const raw = lines[lineNo];
    const semi = raw.indexOf(';');
    if (semi >= 0) {
      const tag = raw.slice(semi + 1).trim();
      if (tag.startsWith('FEATURE:') || tag.startsWith('TYPE:')) role = roleOf(tag.slice(tag.indexOf(':') + 1));
      else if (tag === 'CHANGE_LAYER' || tag === 'LAYER_CHANGE') {
        layer++;
        layerStart[layer] = c.n;
        layerZ[layer] = layerZ[layer - 1] ?? 0;
      } else if (tag.startsWith('Z_HEIGHT:') || tag.startsWith('Z:')) {
        if (layer >= 0) layerZ[layer] = tagValue(tag);
      } else if (tag.startsWith('LAYER_HEIGHT:') || tag.startsWith('HEIGHT:')) forcedHeight = tagValue(tag) || 0;
      else if (tag.startsWith('LINE_WIDTH:') || tag.startsWith('WIDTH:')) forcedWidth = tagValue(tag) || 0;
      else if (tag === 'WIPE_START') wiping = true;
      else if (tag === 'WIPE_END') wiping = false;
      else if (tag === 'FLUSH_START') flushing = true;
      else if (tag === 'FLUSH_END') flushing = false;
    }
    const code = (semi >= 0 ? raw.slice(0, semi) : raw).trim();
    if (!code) continue;
    const parts = code.split(/\s+/);
    const cmd = parts[0].toUpperCase();
    const p: Record<string, number> = {};
    for (let k = 1; k < parts.length; k++) {
      const letter = parts[k][0]?.toUpperCase();
      if (letter >= 'A' && letter <= 'Z') p[letter] = Number.parseFloat(parts[k].slice(1));
    }
    const target = () => {
      const nx = p.X === undefined ? x : absolute ? p.X : x + p.X;
      const ny = p.Y === undefined ? y : absolute ? p.Y : y + p.Y;
      const nz = p.Z === undefined ? z : absolute ? p.Z : z + p.Z;
      let dE = 0;
      if (p.E !== undefined) {
        dE = absoluteE ? p.E - e : p.E;
        e = absoluteE ? p.E : e + p.E;
      }
      if (Number.isFinite(p.F)) feedrate = p.F / 60;
      return [nx, ny, nz, dE] as const;
    };
    switch (cmd) {
      case 'G0':
      case 'G1': {
        const [nx, ny, nz, dE] = target();
        move(nx, ny, nz, dE, lineNo, false);
        break;
      }
      case 'G2':
      case 'G3': {
        // Arcs are drawn as chords of about 1 mm, as GCodeProcessor discretizes them.
        const [nx, ny, nz, dE] = target();
        const cx = x + (p.I || 0), cy = y + (p.J || 0);
        const r = Math.hypot(x - cx, y - cy);
        let angle = Math.atan2(y - cy, x - cx);
        let sweep = Math.atan2(ny - cy, nx - cx) - angle;
        if (cmd === 'G2' && sweep >= 0) sweep -= 2 * Math.PI;
        if (cmd === 'G3' && sweep <= 0) sweep += 2 * Math.PI;
        const steps = r > 0 ? Math.min(64, Math.max(1, Math.ceil(Math.abs(sweep) * r))) : 1;
        const z0 = z;
        for (let s = 1; s <= steps; s++) {
          const last = s === steps;
          angle += sweep / steps;
          move(last ? nx : cx + r * Math.cos(angle), last ? ny : cy + r * Math.sin(angle), z0 + ((nz - z0) * s) / steps, dE / steps, lineNo, !last);
        }
        break;
      }
      case 'G4':
        dwell += Number.isFinite(p.S) ? p.S : (p.P || 0) / 1000;
        break;
      case 'G90':
        absolute = absoluteE = true;
        break;
      case 'G91':
        absolute = absoluteE = false;
        break;
      case 'M82':
        absoluteE = true;
        break;
      case 'M83':
        absoluteE = false;
        break;
      case 'G92':
        if (p.E !== undefined) e = p.E;
        if (p.X !== undefined) x = p.X;
        if (p.Y !== undefined) y = p.Y;
        if (p.Z !== undefined) z = p.Z;
        break;
      case 'M106': // Bambu printers use both M106 and M106 P1 for the part fan (process_M106)
        if (p.P === undefined || p.P === 1) fan = p.S === undefined ? 100 : (100 / 255) * p.S;
        break;
      case 'M107':
        if (p.P === undefined || p.P === 1) fan = 0;
        break;
      case 'M104':
      case 'M109':
        if (Number.isFinite(p.S)) temps[Number.isFinite(p.T) ? p.T : extruder] = p.S;
        break;
      case 'M204':
        if (Number.isFinite(p.S)) accel = travelAccel = p.S;
        if (Number.isFinite(p.P)) accel = p.P;
        if (Number.isFinite(p.T)) travelAccel = p.T;
        break;
      case 'M205':
        if (Number.isFinite(p.X)) jerk = p.X;
        break;
      case 'M900':
        if (Number.isFinite(p.K)) pa = Math.max(0, p.K);
        break;
      case 'SET_VELOCITY_LIMIT': {
        const a = code.match(/ACCEL=([\d.]+)/i);
        const scv = code.match(/SQUARE_CORNER_VELOCITY=([\d.]+)/i);
        if (a) accel = travelAccel = Number(a[1]);
        if (scv) jerk = Number(scv[1]);
        break;
      }
      case 'SET_PRESSURE_ADVANCE': {
        const adv = code.match(/ADVANCE=([\d.]+)/i);
        if (adv) pa = Number(adv[1]);
        break;
      }
      default:
        if (/^T\d+$/.test(cmd)) {
          const n = Number(cmd.slice(1));
          // T255, T1000 and the like are Bambu's special commands, not filaments.
          if (n < 255 && n !== extruder) {
            if (used.size > 0) filamentChanges++;
            extruder = n;
            push(MOVE.toolChange, x, y, z, lineNo, 0, 0, 0);
            typeCount[MOVE.toolChange]++;
          }
        }
    }
  }

  const count = c.n;
  const f = c.f;
  // shortcut: each move's time is a stop-and-go trapezoid, scaled so all moves add up to the slicer's own
  // total; per-role and per-layer times are proportional, not Orca's planner figures. Upgrade by porting
  // GCodeProcessor's time planner.
  let raw = 0;
  for (let i = 0; i < count; i++) raw += f.time[i];
  const target = totalTime > dwell ? totalTime - dwell : 0;
  const scale = raw > 0 && target > 0 ? target / raw : 1;
  if (scale !== 1) for (let i = 0; i < count; i++) f.time[i] *= scale;
  if (!totalTime) totalTime = raw + dwell;

  if (layerStart.length === 0) {
    layerStart.push(0);
    layerZ.push(0);
  }
  layerStart[0] = 0; // the start G-code belongs to the first layer
  const layerTime: number[] = layerStart.map(() => 0);
  const typeTime: number[] = Array(7).fill(0);
  const roleTime: number[] = Array(ROLES.length).fill(0);
  for (let i = 0; i < count; i++) {
    layerTime[c.layer[i]] += f.time[i];
    typeTime[c.type[i]] += f.time[i];
    if (c.type[i] === MOVE.extrude) roleTime[c.role[i]] += f.time[i];
  }
  layerStart.push(count);
  for (let l = 0; l < layerZ.length; l++) {
    for (let i = layerStart[l]; !layerZ[l] && i < layerStart[l + 1]; i++) if (c.type[i] === MOVE.extrude) layerZ[l] = f.z[i];
  }

  const slots = Math.max(densities.length, used.size ? Math.max(...used) + 1 : 0);
  const usedMm = footer['filament used [mm]'] ?? [];
  const usedG = footer['filament used [g]'] ?? [];
  const cost = footer['filament cost'] ?? [];
  const costPerKg = numbers(config.filament_cost);
  for (let s = 0; s < slots; s++) {
    const mm3 = (modelMm3[s] ?? 0) + (supportMm3[s] ?? 0) + (towerMm3[s] ?? 0) + (flushMm3[s] ?? 0);
    usedMm[s] ??= mm3 / area(s);
    usedG[s] ??= (mm3 / 1000) * (densities[s] ?? 1.24);
    cost[s] ??= (usedG[s] / 1000) * (costPerKg[s] ?? 0);
  }

  const cut = <T extends Float32Array | Uint8Array | Uint32Array>(a: T) => a.subarray(0, count) as T;
  return {
    count,
    x: cut(f.x), y: cut(f.y), z: cut(f.z),
    type: cut(c.type), role: cut(c.role), extruder: cut(c.extruder), layer: cut(c.layer), line: cut(c.line),
    width: cut(f.width), height: cut(f.height), feedrate: cut(f.feedrate), fan: cut(f.fan),
    temperature: cut(f.temperature), acceleration: cut(f.acceleration), jerk: cut(f.jerk),
    pressureAdvance: cut(f.pressureAdvance), mm3PerMm: cut(f.mm3PerMm), e: cut(f.e), time: cut(f.time),
    layerStart, layerZ, layerTime, config,
    stats: {
      totalTime,
      prepareTime: modelTime && totalTime > modelTime ? totalTime - modelTime : 0,
      usedMm, usedG, cost,
      modelMm3, supportMm3, towerMm3, flushMm3, roleMm3,
      filamentChanges,
      extruderChanges: numbers(config.nozzle_diameter).length > 1 ? filamentChanges : 0,
      typeDistance, typeCount, typeTime, roleTime,
      usedExtruders: [...used].sort((a, b) => a - b),
      densities, diameters,
    },
  };
}
