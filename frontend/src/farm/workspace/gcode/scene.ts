// Fork (printfarm): the Preview's toolpaths in three.js. The parser's columns go to the GPU as they are, as
// instanced attributes shared by every object: instance k is move k + 1, from the end of move k (its start) to
// its own end. Extrusions are lit boxes built in the vertex shader from the move's ends, width and height;
// travels and wipes are lines; retractions, unretractions and seams points. The sliders and show/hide only change
// uniforms (each instance knows its move index, gl_InstanceID + 1, as an integer), and a new view type only
// rewrites the colour buffer. The memory per move is the parser's columns plus 4 bytes of colour.
import * as THREE from 'three';
import { MOVE, type Toolpaths } from './parse';

type RGB = [number, number, number];
export type ShownOptions = Record<'travel' | 'wipe' | 'retract' | 'unretract' | 'seam', boolean>;

const COMMON = /* glsl */ `
attribute vec3 aStart;
attribute vec3 aEnd;
attribute float aType;
attribute float aRole;
uniform int uLo;
uniform int uHi;
uniform int uRoles;
uniform int uType;
uniform float uDepth;
bool hidden() {
  int move = gl_InstanceID + 1;
  // the legend's role switches hide extrusions only (travels and the rest keep the last role, not shown)
  return move < uLo || move > uHi || int(aType + 0.5) != uType || (uType == 0 && ((uRoles >> int(aRole + 0.5)) & 1) == 0);
}
vec3 scenePos(vec3 p) { return vec3(p.x, p.z, uDepth - p.y); }
`;
const OFF = 'gl_Position = vec4(2.0, 2.0, 2.0, 1.0);';

const EXTRUSION_VERTEX = /* glsl */ `${COMMON}
attribute float aW;
attribute float aH;
attribute vec4 aColor;
varying vec3 vColor;
varying vec3 vNormal;
void main() {
  if (hidden()) { ${OFF} return; }
  vec3 a = scenePos(aStart);
  vec3 b = scenePos(aEnd);
  vec3 d = b - a;
  float len = length(d);
  vec3 dir = len > 0.0 ? d / len : vec3(1.0, 0.0, 0.0);
  vec3 side = vec3(-dir.z, 0.0, dir.x);
  side = length(side) > 1e-6 ? normalize(side) : vec3(0.0, 0.0, 1.0);
  vec3 up = cross(side, dir);
  float w = aW * 0.001;
  float h = aH * 0.001;
  // the box: along the move (overlapping the joints by half a width), below the nozzle, as wide as the line
  vec3 p = a - dir * (w * 0.25) + dir * position.x * (len + w * 0.5) + up * (position.y - 1.0) * h + side * position.z * w;
  vNormal = normalize(normalMatrix * (up * normal.y + side * normal.z));
  vColor = aColor.rgb;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}`;
const EXTRUSION_FRAGMENT = /* glsl */ `
varying vec3 vColor;
varying vec3 vNormal;
void main() {
  float diffuse = max(dot(normalize(vNormal), normalize(vec3(0.3, 1.0, 0.6))), 0.0);
  gl_FragColor = vec4(vColor * (0.45 + 0.6 * diffuse), 1.0);
}`;
const LINE_VERTEX = /* glsl */ `${COMMON}
attribute vec4 aColor;
varying vec3 vColor;
void main() {
  if (hidden()) { ${OFF} return; }
  vColor = aColor.rgb;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(mix(scenePos(aStart), scenePos(aEnd), position.x), 1.0);
}`;
const LINE_FRAGMENT = /* glsl */ `
varying vec3 vColor;
void main() { gl_FragColor = vec4(vColor, 1.0); }`;
const POINT_VERTEX = /* glsl */ `${COMMON}
void main() {
  if (hidden()) { ${OFF} return; }
  gl_PointSize = 6.0;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(scenePos(aEnd), 1.0);
}`;
const POINT_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
void main() { gl_FragColor = vec4(uColor, 1.0); }`;

/** The four long faces of a box (the ends are hidden by the next segment): x along the move 0..1, y up 0..1
 * (the top at the nozzle), z across -0.5..0.5, with flat normals. */
function boxSides(): THREE.BufferGeometry {
  const faces: [number[][], number[]][] = [
    [[[0, 1, -0.5], [1, 1, -0.5], [1, 1, 0.5], [0, 1, 0.5]], [0, 1, 0]],
    [[[0, 0, 0.5], [1, 0, 0.5], [1, 0, -0.5], [0, 0, -0.5]], [0, -1, 0]],
    [[[0, 0, 0.5], [0, 1, 0.5], [1, 1, 0.5], [1, 0, 0.5]], [0, 0, 1]],
    [[[1, 0, -0.5], [1, 1, -0.5], [0, 1, -0.5], [0, 0, -0.5]], [0, 0, -1]],
  ];
  const pos: number[] = [], nor: number[] = [], idx: number[] = [];
  faces.forEach(([quad, n], f) => {
    quad.forEach((v) => (pos.push(...v), nor.push(...n)));
    idx.push(4 * f, 4 * f + 2, 4 * f + 1, 4 * f, 4 * f + 3, 4 * f + 2);
  });
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('normal', new THREE.Float32BufferAttribute(nor, 3));
  g.setIndex(idx);
  return g;
}

export class ToolpathScene {
  readonly group = new THREE.Group();
  readonly marker: THREE.Mesh;
  /** RGBA per move, written by ``legend.paint``; call ``colorsChanged`` after. */
  readonly colors: Uint8Array;
  private readonly colorBuffer: THREE.InstancedInterleavedBuffer;
  private readonly geometries: THREE.InstancedBufferGeometry[] = [];
  private readonly objects: Record<'extrude' | 'travel' | 'wipe' | 'retract' | 'unretract' | 'seam', THREE.Mesh | THREE.LineSegments | THREE.Points>;
  private readonly uniforms = {
    uLo: { value: 0 },
    uHi: { value: -1 },
    uRoles: { value: 0 },
    uDepth: { value: 0 },
  };
  private readonly tp: Toolpaths;
  private readonly depth: number;

  constructor(tp: Toolpaths, depth: number) {
    this.tp = tp;
    this.depth = depth;
    this.uniforms.uDepth.value = depth;
    this.colors = new Uint8Array(tp.count * 4);
    const instances = Math.max(0, tp.count - 1);
    // one move later than the start: instance k reads move k + 1's attributes
    const shifted = (array: THREE.TypedArray, size: number, normalized = false) =>
      new THREE.InterleavedBufferAttribute(new THREE.InstancedInterleavedBuffer(array, size, 1), size, size, normalized);
    const posBuffer = new THREE.InstancedInterleavedBuffer(tp.pos, 3, 1);
    this.colorBuffer = new THREE.InstancedInterleavedBuffer(this.colors, 4, 1);
    const shared: Record<string, THREE.InterleavedBufferAttribute> = {
      aStart: new THREE.InterleavedBufferAttribute(posBuffer, 3, 0),
      aEnd: new THREE.InterleavedBufferAttribute(posBuffer, 3, 3),
      aType: shifted(tp.type, 1),
      aRole: shifted(tp.role, 1),
      aW: shifted(tp.width, 1),
      aH: shifted(tp.height, 1),
      aColor: new THREE.InterleavedBufferAttribute(this.colorBuffer, 4, 4, true),
    };
    const geometry = (base: THREE.BufferGeometry, names: string[]) => {
      const g = new THREE.InstancedBufferGeometry();
      g.index = base.index;
      for (const [k, v] of Object.entries(base.attributes)) g.setAttribute(k, v);
      for (const k of names) g.setAttribute(k, shared[k]);
      g.instanceCount = instances;
      this.geometries.push(g);
      return g;
    };
    const material = (vertexShader: string, fragmentShader: string, type: number, extra: Record<string, THREE.IUniform> = {}) =>
      new THREE.ShaderMaterial({
        vertexShader,
        fragmentShader,
        uniforms: { ...this.uniforms, uType: { value: type }, ...extra },
      });
    const common = ['aStart', 'aEnd', 'aType', 'aRole'];
    const line = new THREE.BufferGeometry();
    line.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0], 3));
    const point = new THREE.BufferGeometry();
    point.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0], 3));
    const lines = (type: number) => new THREE.LineSegments(geometry(line, [...common, 'aColor']), material(LINE_VERTEX, LINE_FRAGMENT, type));
    const points = (type: number, color: RGB) =>
      new THREE.Points(geometry(point, common), material(POINT_VERTEX, POINT_FRAGMENT, type, { uColor: { value: new THREE.Color(...color.map((c) => c / 255)) } }));
    this.objects = {
      extrude: new THREE.Mesh(geometry(boxSides(), [...common, 'aW', 'aH', 'aColor']), material(EXTRUSION_VERTEX, EXTRUSION_FRAGMENT, MOVE.extrude)),
      travel: lines(MOVE.travel),
      wipe: lines(MOVE.wipe),
      retract: points(MOVE.retract, [205, 34, 214]),
      unretract: points(MOVE.unretract, [73, 173, 207]),
      seam: points(MOVE.seam, [230, 230, 230]),
    };
    for (const o of Object.values(this.objects)) {
      o.frustumCulled = false;
      this.group.add(o);
    }

    // The nozzle: a cone pointing down at the current move's end.
    const cone = new THREE.ConeGeometry(1.2, 4, 16).rotateX(Math.PI);
    cone.translate(0, 2, 0);
    this.marker = new THREE.Mesh(cone, new THREE.MeshLambertMaterial({ color: 0xffffff, transparent: true, opacity: 0.8 }));
    this.group.add(this.marker);
  }

  /** Upload the colours again after ``colors`` changed. */
  colorsChanged() {
    this.colorBuffer.needsUpdate = true;
  }

  /** Show moves ``lo``..``hi`` (move indices, inclusive), the roles in ``roles`` (bit per role) and the options. */
  show(lo: number, hi: number, roles: number, options: ShownOptions) {
    this.uniforms.uLo.value = lo;
    this.uniforms.uHi.value = hi;
    this.uniforms.uRoles.value = roles;
    // instances past the last move shown aren't drawn at all
    const count = Math.max(0, Math.min(this.tp.count - 1, hi));
    for (const g of this.geometries) g.instanceCount = count;
    for (const k of ['travel', 'wipe', 'retract', 'unretract', 'seam'] as const) this.objects[k].visible = options[k];
    const at = Math.max(0, Math.min(hi, this.tp.count - 1));
    const p = this.tp.pos;
    this.marker.position.set(p[3 * at], p[3 * at + 2], this.depth - p[3 * at + 1]);
  }

  dispose() {
    for (const o of Object.values(this.objects)) (o.material as THREE.Material).dispose();
    for (const g of this.geometries) g.dispose();
    this.marker.geometry.dispose();
    (this.marker.material as THREE.Material).dispose();
  }
}
