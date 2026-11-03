// Printfarm (fork): what Accoda proposes from Seller Hub's stock, so nothing already in stock is printed by hand.
import type { FarmPlanComponent } from './api';

const URGENCY: Record<string, number> = { esaurito: 0, urgente: 1, da_stampare: 2 };

/** Products one component's stock makes (stock ÷ pieces per product); null when the stock is unknown. */
export const kitUnits = (c: FarmPlanComponent) =>
  c.stock == null ? null : Math.floor(c.stock / Math.max(1, c.kit_quantity ?? 1));

export interface Suggestion {
  order: FarmPlanComponent[];
  picked: string[];
  limiting: string | null; // the kit's piece with the fewest products in stock
  known: boolean; // Seller Hub's stock was read
}

/** Out of stock and urgent first, then the fewest products in stock; ticked: what Seller Hub says to print.
 *  Without Seller Hub's stock the order stays and what is missing (else everything) is ticked, as before. */
export function suggestPrints(components: FarmPlanComponent[], byHand: boolean): Suggestion {
  const known = components.some((c) => c.stock != null);
  if (!known) {
    const missing = components.filter((c) => c.missing > 0);
    const picked = (byHand && missing.length ? missing : components).map((c) => c.code);
    return { order: components, picked, limiting: null, known };
  }
  const units = (c: FarmPlanComponent) => kitUnits(c) ?? Infinity;
  const order = [...components].sort(
    (a, b) => (URGENCY[a.status] ?? 9) - (URGENCY[b.status] ?? 9) || units(a) - units(b),
  );
  const fewest = Math.min(...components.map(units));
  const limiting = byHand && components.length > 1 && Number.isFinite(fewest)
    ? order.find((c) => units(c) === fewest)?.code ?? null
    : null;
  return { order, picked: order.filter((c) => c.missing > 0).map((c) => c.code), limiting, known };
}
