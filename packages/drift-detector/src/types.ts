/**
 * Types for drift detection comparison logic.
 */

export type DriftSeverity = "breaking" | "warning" | "info";

export interface DeclaredField {
  name: string;
  type: string;
  required: boolean;
}

/** Represents one discovered divergence between implementation and contract */
export interface DriftFinding {
  severity: DriftSeverity;
  description: string;
  fields: string[];
}

/** A pairing of implementation URN to contract endpoint URN to compare */
export interface EndpointContractPair {
  implementationUrn: string;
  implementationPath: string;
  implementationMethod: string;
  contractEndpointUrn: string;
  contractPath: string;
  contractMethod: string;
  /** scope of the pair (for URN construction) */
  scope: string;
}
