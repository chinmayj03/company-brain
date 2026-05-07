/**
 * Billing service — fixture for core-ts extractor tests.
 * Contains: class, interface, function, method, const, import, decorator, enum.
 */

import { EventEmitter } from "events";
import type { PaymentProvider } from "./types";

export const MAX_RETRY_ATTEMPTS = 3;
export const DEFAULT_CURRENCY = "USD";

export interface ChargeRequest {
  amount:   number;
  currency: string;
  userId:   string;
}

export interface ChargeResult {
  success:       boolean;
  transactionId: string;
  errorCode?:    string;
}

export enum BillingStatus {
  Pending   = "pending",
  Completed = "completed",
  Failed    = "failed",
  Refunded  = "refunded",
}

function formatAmount(amount: number, currency: string): string {
  return `${currency} ${amount.toFixed(2)}`;
}

export class BillingService extends EventEmitter {
  private readonly provider: PaymentProvider;
  private retryCount = 0;

  constructor(provider: PaymentProvider) {
    super();
    this.provider = provider;
  }

  async charge(req: ChargeRequest): Promise<ChargeResult> {
    const formatted = formatAmount(req.amount, req.currency);
    this.emit("charge_attempt", { userId: req.userId, amount: formatted });

    try {
      const result = await this.provider.processPayment(req);
      this.retryCount = 0;
      return result;
    } catch (err) {
      if (this.retryCount < MAX_RETRY_ATTEMPTS) {
        this.retryCount++;
        return this.charge(req);
      }
      throw err;
    }
  }

  async refund(transactionId: string): Promise<boolean> {
    return this.provider.refund(transactionId);
  }

  getRetryCount(): number {
    return this.retryCount;
  }
}
