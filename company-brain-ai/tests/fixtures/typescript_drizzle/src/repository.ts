// Repository reading users.email via Drizzle
import { db } from "./db";
import { users } from "./db/schema";
import { eq } from "drizzle-orm";

export class UserRepository {
  async findById(id: number) {
    return db.select().from(users).where(eq(users.id, id)).limit(1);
  }

  async findAll() {
    return db.select({ email: users.email, name: users.name }).from(users);
  }

  async findByEmail(email: string) {
    return db.select().from(users).where(eq(users.email, email)).limit(1);
  }
}
