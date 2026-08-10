import { useSyncExternalStore } from "react";

import { Home } from "@/components/home/Home";
import Workspace from "@/components/workspace/Workspace";
import { resolveDriverKind } from "@/lib/apiDriver";
import { envDriverKind, type DriverKind } from "@/lib/driver";

const noopSubscribe = () => () => {};

/**
 * `/` — Home, or the workspace when there is no deployment behind the seam.
 *
 * Home is made of three live reads: what changed at this load, the
 * monitors somebody pinned, and the ranked worklist the detection feed
 * published. The mock fixture has none of them — no deployment to walk, no
 * monitors to store, no two loads to compare — so a Home rendered against
 * it would be a page of invented tiles, which is the opposite of what this
 * surface is for. The fixture keeps the front door it has always had: the
 * workspace, with its hero and its scripted reference conversation.
 *
 * The kind is read through the same `useSyncExternalStore` pair every other
 * surface uses, so the module default and the first client render agree and
 * nothing flashes one front door before settling on the other.
 */
export function HomeRoute() {
  const driverKind = useSyncExternalStore<DriverKind>(
    noopSubscribe,
    resolveDriverKind,
    envDriverKind,
  );
  if (driverKind === "mock") return <Workspace />;
  return <Home />;
}
