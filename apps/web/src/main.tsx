import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// The typeface first, so its @font-face rules are in the sheet before the
// metric-matched fallbacks and the type scale that were tuned against them.
// Both packages are unicode-range-gated per subset, so only `latin` is
// fetched for this app's content — the same five faces `next/font/google`
// emitted, from the same upstream woff2 files.
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "@/globals.css";

import { App } from "@/App";

// StrictMode because the Next App Router ran in it (`reactStrictMode`
// defaults to true) — the double-invoked effects it surfaces in dev are a
// mount/unmount/remount, which is exactly the driver-remount class M31
// fixed. It compiles away in the production build, so nothing measured for
// parity depends on it.
const container = document.getElementById("root");
if (container === null) throw new Error("#root is missing from index.html");

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
