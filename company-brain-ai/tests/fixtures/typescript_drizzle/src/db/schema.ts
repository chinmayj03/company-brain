// Drizzle schema for ADR-0042 fixture
import { pgTable, serial, text, boolean } from "drizzle-orm/pg-core";

export const users = pgTable("users", {
  id: serial("id").primaryKey(),
  email: text("email").notNull(),
  name: text("name"),
  isActive: boolean("is_active").default(true),
});
