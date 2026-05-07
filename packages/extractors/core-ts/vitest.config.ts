import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    environment: "node",
  },
  resolve: {
    alias: {
      "@company-brain/schema": path.resolve(__dirname, "../../schema/src/index.ts"),
      "@company-brain/graph":  path.resolve(__dirname, "../../graph/src/index.ts"),
    },
  },
});
