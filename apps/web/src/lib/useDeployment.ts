/**
 * THE DEPLOYMENT A SURFACE IS TALKING TO: which driver, and is it up.
 *
 * Every route in this app needs the same three things before it can render
 * anything true — the driver the seam is configured for, that driver
 * installed on the store, and a health heartbeat so the connection pill and
 * the newest data load are facts rather than assumptions. The workspace and
 * Monitors each carry their own copy of this, written before there was a
 * third surface to share it with; Home takes this one rather than adding a
 * fourth transcription of the same forty lines.
 *
 * The two existing copies are deliberately NOT switched over here. Both sit
 * inside components carrying M31/M33 lifecycle invariants (a driver minted
 * per mount, a session that outlives it), and a shared hook is worth having
 * without being worth re-opening those.
 *
 * WHAT THE HEARTBEAT IS FOR, beyond the pill: `watermarkId` is how a client
 * learns a NEW data load has landed, and every Monitors read is keyed by
 * it. A one-shot probe leaves a tab open overnight claiming yesterday's
 * load; the interval is fast while offline (5s, so recovery is quick) and
 * slow while online (30s, so a landing page left open all morning is not a
 * poller).
 */

import { useEffect, useMemo, useSyncExternalStore } from "react";

import { ApiDriver, fetchHealthDetail, resolveDriverKind } from "@/lib/apiDriver";
import { envDriverKind, type DriverKind, type TurnDriver } from "@/lib/driver";
import { MockDriver } from "@/lib/mockDriver";
import { useSessionStore } from "@/lib/store";

const noopSubscribe = () => () => {};

export interface Deployment {
  driverKind: DriverKind;
  driver: TurnDriver;
  /** True when there is a real deployment behind the seam. */
  live: boolean;
}

export function useDeployment(): Deployment {
  // Hydration-safe: the module default is the env's, and the client
  // snapshot honours the localStorage override the tooling writes.
  const driverKind = useSyncExternalStore<DriverKind>(
    noopSubscribe,
    resolveDriverKind,
    envDriverKind,
  );

  const driver = useMemo<TurnDriver>(() => {
    if (driverKind === "api") {
      return new ApiDriver({
        onSession: (session) => useSessionStore.getState().adoptSession(session),
        onConnectionState: (state, detail) =>
          useSessionStore.getState().setConnection({ state, detail }),
        onContractDrift: (paths) => useSessionStore.getState().reportContractDrift(paths),
      });
    }
    return new MockDriver();
  }, [driverKind]);

  const setDriver = useSessionStore((s) => s.setDriver);
  useEffect(() => {
    setDriver(driver);
  }, [driver, setDriver]);

  useEffect(() => {
    const { setConnection } = useSessionStore.getState();
    if (driverKind !== "api") {
      setConnection({ mode: "mock", state: "online", detail: undefined, healthChecked: true });
      return;
    }
    setConnection({ mode: "api", state: "connecting" });
    let cancelled = false;
    let timer: number | undefined;
    const probe = async (): Promise<void> => {
      const health = await fetchHealthDetail();
      if (cancelled) return;
      setConnection({
        state: health.ok ? "online" : "offline",
        llmMode: health.llmMode,
        storeMode: health.storeMode,
        authMode: health.authMode,
        newestWatermarkId: health.watermarkId,
        // The badge is a claim about the deployment; it waits for the
        // deployment to have answered.
        healthChecked: true,
      });
      timer = window.setTimeout(() => void probe(), health.ok ? 30_000 : 5_000);
    };
    void probe();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [driverKind]);

  return { driverKind, driver, live: driverKind === "api" };
}
