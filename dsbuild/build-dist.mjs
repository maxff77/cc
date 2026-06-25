// Builds the Ranger-X DS runtime entry the design-sync converter consumes.
// react/react-dom stay external — the converter wraps this into the
// window.RangerX IIFE and provides React from _vendor.
import { build } from "esbuild";

await build({
  entryPoints: ["src/index.jsx"],
  outfile: "dist/index.js",
  bundle: true,
  format: "esm",
  jsx: "transform", // classic runtime; React is imported in the entry
  external: ["react", "react-dom"],
  logLevel: "info",
});
