import type { NextConfig } from "next";
const development = process.env.NODE_ENV === "development";
const config: NextConfig = {
  ...(development
    ? {
        async rewrites() {
          return [
            {
              source: "/api/:path*",
              destination: "http://127.0.0.1:8010/api/:path*",
            },
          ];
        },
      }
    : { output: "export" }),
  images: { unoptimized: true },
  poweredByHeader: false,
};
export default config;
