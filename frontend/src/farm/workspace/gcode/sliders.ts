// Fork (printfarm): the Preview sliders' state, as OrcaSlicer's IMSlider and GLCanvas3D::on_key move it: two
// layer thumbs (one active), one layer mode, and the move slider over the top layer's moves, which steps into
// the layer before or after at its ends.

export interface SliderState {
  /** Lowest and highest layer shown (0-based). */
  lo: number;
  hi: number;
  active: 'lo' | 'hi';
  oneLayer: boolean;
  /** Position on the move slider; null: the top layer's last move. */
  move: number | null;
}

export type SliderAction =
  | { type: 'reset' }
  | { type: 'set'; lo: number; hi: number }
  | { type: 'step'; delta: number }
  | { type: 'jump'; layer: number }
  | { type: 'active'; which: 'lo' | 'hi' }
  | { type: 'oneLayer' }
  | { type: 'moveTo'; index: number | null }
  /** ``moves(layer)``: how many moves the move slider has on that layer. */
  | { type: 'move'; delta: number; moves: (layer: number) => number };

export const initialSliders = (layers: number): SliderState => ({ lo: 0, hi: Math.max(0, layers - 1), active: 'hi', oneLayer: false, move: null });

export function sliderReducer(layers: number) {
  const clamp = (v: number) => Math.max(0, Math.min(layers - 1, v));
  const set = (s: SliderState, lo: number, hi: number, move: number | null = null): SliderState => {
    const h = clamp(hi);
    return { ...s, hi: h, lo: s.oneLayer ? h : Math.min(clamp(lo), h), move };
  };
  return (s: SliderState, a: SliderAction): SliderState => {
    switch (a.type) {
      case 'reset':
        return initialSliders(layers);
      case 'set':
        return set(s, a.lo, a.hi);
      case 'step':
        return s.oneLayer || s.active === 'hi' ? set(s, s.lo, s.hi + a.delta) : set(s, Math.min(s.lo + a.delta, s.hi), s.hi);
      case 'jump':
        return s.oneLayer || s.active === 'hi' ? set(s, s.lo, a.layer) : set(s, a.layer, s.hi);
      case 'active':
        return { ...s, active: a.which };
      case 'oneLayer':
        return { ...s, oneLayer: !s.oneLayer, lo: s.oneLayer ? 0 : s.hi, move: null };
      case 'moveTo':
        return { ...s, move: a.index };
      case 'move': {
        const count = a.moves(s.hi);
        const at = s.move == null ? count - 1 : Math.min(s.move, count - 1);
        const next = at + a.delta;
        if (next < 0 && s.hi > 0) return set(s, s.lo, s.hi - 1);
        if (next >= count && s.hi < layers - 1) return set(s, s.lo, s.hi + 1, 0);
        return { ...s, move: Math.max(0, Math.min(count - 1, next)) };
      }
    }
  };
}
