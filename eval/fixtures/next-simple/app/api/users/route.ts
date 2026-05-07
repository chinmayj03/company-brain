/**
 * eval/fixtures/next-simple/app/api/users/route.ts
 * Users API route — fixture for framework-next extractor tests.
 */
import { NextRequest, NextResponse } from "next/server";

export async function GET(req: NextRequest): Promise<NextResponse> {
  return NextResponse.json({ users: [] });
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const body = await req.json();
  return NextResponse.json({ created: true, id: "u_001" }, { status: 201 });
}
