/**
 * Printfarm (fork): what Accoda proposes from Seller Hub's stock. All data is made up.
 */

import { describe, it, expect } from 'vitest';
import type { FarmPlanComponent } from '../../farm/api';
import { kitUnits, suggestPrints } from '../../farm/suggest';

const part = (code: string, over: Partial<FarmPlanComponent>): FarmPlanComponent => ({
  code, name: code, status: '', to_print: 0, stock: null, need: null, days_left: null, color: 'Giallo',
  color_hex: 'f5d000', shape: null, shape_id: null, known: true, products: [], in_queue: 0, missing: 0,
  plates: [], kit_quantity: 1, ...over,
});

describe('suggestPrints', () => {
  // A set of handles: 2 big ones and 2 small ones per product.
  const big = part('C-0094', { stock: 12, kit_quantity: 2, to_print: 0 });
  const small = part('C-0095', { stock: 3, kit_quantity: 2, to_print: 8, missing: 8, status: 'urgente' });
  const cap = part('C-0096', { stock: 0, to_print: 4, missing: 4, status: 'esaurito' });

  it('ticks what Seller Hub says to print, most urgent first, and finds the piece that limits the kit', () => {
    expect(kitUnits(big)).toBe(6);
    expect(kitUnits(small)).toBe(1);
    const s = suggestPrints([big, small, cap], true);
    expect(s.order.map((c) => c.code)).toEqual(['C-0096', 'C-0095', 'C-0094']);
    expect(s.picked).toEqual(['C-0096', 'C-0095']); // 12 big ones in stock: not printed again
    expect(s.limiting).toBe('C-0096'); // no product can be made without it
    expect(s.known).toBe(true);
  });

  it('within the same urgency the fewest products in stock come first', () => {
    const a = part('A', { stock: 10, to_print: 1, missing: 1, status: 'da_stampare' });
    const b = part('B', { stock: 4, to_print: 1, missing: 1, status: 'da_stampare' });
    expect(suggestPrints([a, b], true).order.map((c) => c.code)).toEqual(['B', 'A']);
    expect(suggestPrints([a], true).limiting).toBeNull(); // one part: not a kit
  });

  it('without Seller Hub keeps the order and ticks what is missing, else everything, as before', () => {
    const a = part('A', {});
    const b = part('B', {});
    expect(suggestPrints([a, b], true)).toEqual({ order: [a, b], picked: ['A', 'B'], limiting: null, known: false });
    const c = part('C', { missing: 3 });
    expect(suggestPrints([a, c], true).picked).toEqual(['C']);
  });
});
