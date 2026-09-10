import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output bundles a minimal server + only the deps actually
  // used, so the production Docker image doesn't need a full `npm install`.
  output: "standalone",
  devIndicators: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
