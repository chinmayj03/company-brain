/**
 * Shared types — fixture for core-ts extractor tests.
 */

export interface PaymentProvider {
  processPayment(req: { amount: number; currency: string; userId: string }): Promise<{
    success: boolean;
    transactionId: string;
    errorCode?: string;
  }>;
  refund(transactionId: string): Promise<boolean>;
}

export type Currency = "USD" | "EUR" | "GBP";

export interface User {
  id:        string;
  email:     string;
  plan:      "free" | "pro" | "enterprise";
  createdAt: Date;
}
