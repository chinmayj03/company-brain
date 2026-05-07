/**
 * packages/repo-map/src/token-budget.ts
 *
 * Token budget utilities.
 *
 * We use a simple 4-chars-per-token approximation (conservative; real
 * tokenizers average ~3.5 chars/token for code but vary by language).
 * The approximation is intentionally conservative so we never exceed the
 * caller's stated budget.
 */

/** Approximate token count for a string. */
export function countTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

/** Budget allocator: given total budget and N items, allocate per-item budgets
 *  proportionally by weight (e.g. in-degree), with a minimum floor. */
export function allocateBudgets(
  totalBudget: number,
  items:       Array<{ weight: number }>,
  minPerItem:  number = 20,
): number[] {
  if (items.length === 0) return [];

  const totalWeight = items.reduce((s, i) => s + Math.max(i.weight, 1), 0);
  const budgets: number[] = items.map(item => {
    const fraction = Math.max(item.weight, 1) / totalWeight;
    return Math.max(Math.floor(fraction * totalBudget), minPerItem);
  });

  // Scale down if we exceed total
  const allocated = budgets.reduce((s, b) => s + b, 0);
  if (allocated > totalBudget) {
    const scale = totalBudget / allocated;
    return budgets.map(b => Math.max(Math.floor(b * scale), minPerItem));
  }
  return budgets;
}

/** Truncate text to fit within a token budget, adding ellipsis if cut. */
export function fitToBudget(text: string, tokenBudget: number): { text: string; truncated: boolean } {
  const maxChars = tokenBudget * 4;
  if (text.length <= maxChars) return { text, truncated: false };
  // Cut at last newline within budget
  const cut = text.slice(0, maxChars);
  const lastNl = cut.lastIndexOf("\n");
  const trimmed = lastNl > 0 ? cut.slice(0, lastNl) : cut;
  return { text: trimmed + "\n  ... (truncated)", truncated: true };
}
