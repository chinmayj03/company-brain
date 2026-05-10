// Next.js route handler — GET /api/users
import { NextResponse } from "next/server";
import { UserRepository } from "../../../repository";

const repo = new UserRepository();

export async function GET(request: Request) {
  const users = await repo.findAll();
  return NextResponse.json(users);
}

export async function POST(request: Request) {
  const body = await request.json();
  // create user
  return NextResponse.json({ ok: true });
}
