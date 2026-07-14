import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin file tracing to this app so Next.js does not pick up stray
  // lockfiles elsewhere on the machine.
  outputFileTracingRoot: path.join(__dirname),
};

export default nextConfig;
