import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
  cacheHandler: require.resolve("./desktop-cache.cjs"),
  cacheMaxMemorySize: 0,
  images: { unoptimized: true },
  outputFileTracingExcludes: {
    "/*": [".env", ".env.*", "**/PODCUTAI-InstallerDev/**"],
  },
};
export default config;
