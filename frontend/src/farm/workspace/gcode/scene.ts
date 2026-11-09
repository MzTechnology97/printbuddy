// Fork (printfarm): the Preview's toolpaths in three.js. Extrusions are lit boxes (one instance per move, sized
// by its width and height), travels and wipes lines, retractions and seams points. Everything is built once;
// the layer/move sliders and the legend's show/hide only change shader uniforms (each vertex carries its move
// index and role), and a new view type only rewrites the colours.
import * as THREE from 'three';
import { MOVE, type Toolpaths } from './parse';

type RGB = [number, number, number];

/** Scene coordinates of a slicer point: y up, the bed's front edge (slicer y = 0) at z = bed depth. */
export const toScene = (x: number, y: number, z: number, depth: number) => new THREE.Vector3(x, z, depth - y);

const ALWAYS = 30; // role bit always set: moves that aren't extrusions

interface Uniforms {
  uLo: { value: number };
  uHi: { value: number };
  uRoles: { value: number };
}

function ranged<T extends THREE.Material>(material: T, u: Uniforms): T {
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, u);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nattribute float aMove;\nattribute float aRole;\nuniform float uLo;\nuniform float uHi;\nuniform int uRoles;')
      .replace(
        '#include <project_vertex>',
        '#include <project_vertex>\nif (aMove < uLo || aMove > uHi || ((uRoles >> int(aRole)) & 1) == 0) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);',
      );
  };
  return material;
}

export class ToolpathScene {
  readonly group = new THREE.Group();
  readonly marker: THREE.Mesh;
  private readonly u: Uniforms = { uLo: { value: 0 }, uHi: { value: -1 }, uRoles: { value: 0 } };
  private readonly extrusions: THREE.InstancedMesh;
  /** Move index of each extrusion instance, and of each vertex of the other objects. */
  private readonly extrusionMoves: Uint32Array;
  private readonly lines: Record<'travel' | 'wipe', { object: THREE.LineSegments; moves: Uint32Array }>;
  private readonly points: Record<'retract' | 'unretract' | 'seam', { object: THREE.Points; moves: Uint32Array }>;

  private readonly tp: Toolpaths;
  private readonly depth: number;

  constructor(tp: Toolpaths, depth: number) {
    this.tp = tp;
    this.depth = depth;
    const { count, type } = tp;
    const ext: number[] = [];
    const travel: number[] = [];
    const wipe: number[] = [];
    const pts: Record<'retract' | 'unretract' | 'seam', number[]> = { retract: [], unretract: [], seam: [] };
    for (let i = 1; i < count; i++) {
      const t = type[i];
      if (t === MOVE.extrude) ext.push(i);
      else if (t === MOVE.travel) travel.push(i);
      else if (t === MOVE.wipe) wipe.push(i);
      else if (t === MOVE.retract) pts.retract.push(i);
      else if (t === MOVE.unretract) pts.unretract.push(i);
      else if (t === MOVE.seam) pts.seam.push(i);
    }

    // Extrusions: a unit box per move, scaled to its length, height and width, sitting below the nozzle.
    const box = new THREE.BoxGeometry(1, 1, 1);
    const material = ranged(new THREE.MeshLambertMaterial(), this.u);
    this.extrusions = new THREE.InstancedMesh(box, material, Math.max(ext.length, 1));
    this.extrusions.count = ext.length;
    this.extrusions.frustumCulled = false;
    this.extrusionMoves = Uint32Array.from(ext);
    const m = this.extrusions.instanceMatrix.array as Float32Array;
    const aMove = new Float32Array(Math.max(ext.length, 1));
    const aRole = new Float32Array(Math.max(ext.length, 1));
    ext.forEach((i, k) => {
      const ax = tp.x[i - 1], ay = tp.y[i - 1], az = tp.z[i - 1];
      // scene axes: x, up (slicer z), depth - y
      let dx = tp.x[i] - ax, dy = tp.z[i] - az, dz = -(tp.y[i] - ay);
      const len = Math.hypot(dx, dy, dz) || 1e-6;
      dx /= len; dy /= len; dz /= len;
      // side vector (width): horizontal, perpendicular to the move; up = side × direction
      let sx = -dz, sz = dx;
      const sl = Math.hypot(sx, sz) || 1;
      sx /= sl; sz /= sl;
      const ux = -sz * dy, uy = sz * dx - sx * dz, uz = sx * dy;
      const w = tp.width[i], h = tp.height[i];
      const L = len + w * 0.5; // overlap the joints
      const o = k * 16;
      m[o] = dx * L; m[o + 1] = dy * L; m[o + 2] = dz * L; m[o + 3] = 0;
      m[o + 4] = ux * h; m[o + 5] = uy * h; m[o + 6] = uz * h; m[o + 7] = 0;
      m[o + 8] = sx * w; m[o + 9] = 0; m[o + 10] = sz * w; m[o + 11] = 0;
      m[o + 12] = (ax + tp.x[i]) / 2;
      m[o + 13] = (az + tp.z[i]) / 2 - h / 2;
      m[o + 14] = depth - (ay + tp.y[i]) / 2;
      m[o + 15] = 1;
      aMove[k] = i;
      aRole[k] = tp.role[i];
    });
    this.extrusions.instanceMatrix.needsUpdate = true;
    box.setAttribute('aMove', new THREE.InstancedBufferAttribute(aMove, 1));
    box.setAttribute('aRole', new THREE.InstancedBufferAttribute(aRole, 1));
    this.extrusions.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(ext.length, 1) * 3), 3);
    this.group.add(this.extrusions);

    const line = (moves: number[]) => {
      const pos = new Float32Array(moves.length * 6);
      const am = new Float32Array(moves.length * 2);
      moves.forEach((i, k) => {
        pos.set([tp.x[i - 1], tp.z[i - 1], depth - tp.y[i - 1], tp.x[i], tp.z[i], depth - tp.y[i]], k * 6);
        am[2 * k] = am[2 * k + 1] = i;
      });
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(moves.length * 6), 3));
      g.setAttribute('aMove', new THREE.BufferAttribute(am, 1));
      g.setAttribute('aRole', new THREE.BufferAttribute(new Float32Array(moves.length * 2).fill(ALWAYS), 1));
      const object = new THREE.LineSegments(g, ranged(new THREE.LineBasicMaterial({ vertexColors: true }), this.u));
      object.frustumCulled = false;
      this.group.add(object);
      return { object, moves: Uint32Array.from(moves) };
    };
    this.lines = { travel: line(travel), wipe: line(wipe) };

    const point = (moves: number[], color: RGB) => {
      const pos = new Float32Array(moves.length * 3);
      moves.forEach((i, k) => pos.set([tp.x[i], tp.z[i], depth - tp.y[i]], k * 3));
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      g.setAttribute('aMove', new THREE.BufferAttribute(Float32Array.from(moves), 1));
      g.setAttribute('aRole', new THREE.BufferAttribute(new Float32Array(moves.length).fill(ALWAYS), 1));
      const mat = ranged(new THREE.PointsMaterial({ size: 6, sizeAttenuation: false, color: new THREE.Color(...color.map((c) => c / 255)) }), this.u);
      const object = new THREE.Points(g, mat);
      object.frustumCulled = false;
      this.group.add(object);
      return { object, moves: Uint32Array.from(moves) };
    };
    this.points = {
      retract: point(pts.retract, [205, 34, 214]),
      unretract: point(pts.unretract, [73, 173, 207]),
      seam: point(pts.seam, [230, 230, 230]),
    };

    // The nozzle: a cone pointing down at the current move's end.
    this.marker = new THREE.Mesh(new THREE.ConeGeometry(1.2, 4, 16).rotateX(Math.PI), new THREE.MeshLambertMaterial({ color: 0xffffff, transparent: true, opacity: 0.8 }));
    this.marker.geometry.translate(0, 2, 0);
    this.group.add(this.marker);
  }

  /** Colour every move with ``color`` (extrusions, travels and wipes; points keep their option colour). */
  recolor(color: (i: number) => RGB) {
    const c = this.extrusions.instanceColor!.array as Float32Array;
    this.extrusionMoves.forEach((i, k) => {
      const [r, g, b] = color(i);
      c[3 * k] = r / 255; c[3 * k + 1] = g / 255; c[3 * k + 2] = b / 255;
    });
    this.extrusions.instanceColor!.needsUpdate = true;
    for (const { object, moves } of Object.values(this.lines)) {
      const attr = object.geometry.getAttribute('color') as THREE.BufferAttribute;
      const a = attr.array as Float32Array;
      moves.forEach((i, k) => {
        const [r, g, b] = color(i);
        a.set([r / 255, g / 255, b / 255, r / 255, g / 255, b / 255], k * 6);
      });
      attr.needsUpdate = true;
    }
  }

  /** Show moves ``lo``..``hi`` (move indices, inclusive), the roles in ``roles`` (bit per role) and the options. */
  show(lo: number, hi: number, roles: number, options: Record<'travel' | 'wipe' | 'retract' | 'unretract' | 'seam', boolean>) {
    this.u.uLo.value = lo;
    this.u.uHi.value = hi;
    this.u.uRoles.value = roles | (1 << ALWAYS);
    this.lines.travel.object.visible = options.travel;
    this.lines.wipe.object.visible = options.wipe;
    this.points.retract.object.visible = options.retract;
    this.points.unretract.object.visible = options.unretract;
    this.points.seam.object.visible = options.seam;
    const at = Math.max(0, Math.min(hi, this.tp.count - 1));
    this.marker.position.copy(toScene(this.tp.x[at], this.tp.y[at], this.tp.z[at], this.depth));
  }

  /** The model's extent, for framing the camera. */
  box(): THREE.Box3 {
    const box = new THREE.Box3();
    const tp = this.tp;
    for (const i of this.extrusionMoves) box.expandByPoint(new THREE.Vector3(tp.x[i], tp.z[i], this.depth - tp.y[i]));
    return box;
  }

  dispose() {
    this.group.traverse((o) => {
      const mesh = o as THREE.Mesh;
      mesh.geometry?.dispose();
      const mat = mesh.material as THREE.Material | THREE.Material[] | undefined;
      (Array.isArray(mat) ? mat : mat ? [mat] : []).forEach((m) => m.dispose());
    });
    this.extrusions.dispose();
  }
}
