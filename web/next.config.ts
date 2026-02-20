import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  basePath: "/trader",
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
