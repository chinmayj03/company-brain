/**
 * Strip technical URN prefix — return the final human-readable name segment.
 *
 * Handles both formats returned by the brain API:
 *   "network-iq-backend-java::component::CompetitivenessPlanEntity"  → "CompetitivenessPlanEntity"
 *   "urn:cb:dev:code:network-iq-backend-java:api_contract:X.getY"    → "X.getY"
 */
export function stripUrn(urn: string): string {
  if (!urn) return urn;
  if (urn.includes('::')) {
    const parts = urn.split('::').filter(Boolean);
    return parts[parts.length - 1];
  }
  const parts = urn.split(':').filter(Boolean);
  return parts[parts.length - 1];
}

/** Infer human role from entity class name. */
export function inferRole(name: string): string {
  const n = name.toLowerCase();
  if (n.includes('controller')) return 'controller';
  if (n.includes('repository'))  return 'repository';
  if (n.includes('service'))     return 'service';
  if (n.includes('dto'))         return 'DTO';
  if (n.includes('entity'))      return 'ORM entity';
  if (n.includes('mapper'))      return 'mapper';
  if (n.includes('config'))      return 'config';
  if (n.includes('filter'))      return 'filter';
  return 'class';
}

/** Map confidence float (0–1) to a risk weight label. */
export function confidenceToWeight(confidence: number): 'high' | 'med' | 'low' {
  if (confidence >= 0.78) return 'high';
  if (confidence >= 0.60) return 'med';
  return 'low';
}
