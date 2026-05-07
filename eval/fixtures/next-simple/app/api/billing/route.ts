/**
 * eval/fixtures/next-simple/app/api/billing/route.ts
 * Billing API route — fixture for framework-next extractor tests.
 * Deliberately returns a trimmed payload to exercise drift detection.
 */
import { NextRequest, NextResponse } from "next/server";

export interface ChargePayload {
  amount:   number;
  currency: string;
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const body: ChargePayload = await req.json();
  // Returns { success, transactionId } — matches ContractEndpoint response schema
  return NextResponse.json({ success: true, transactionId: "txn_001" });
}
