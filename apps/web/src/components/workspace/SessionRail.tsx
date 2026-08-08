"use client";

import { FlaskConical, MessagesSquare, Play, RefreshCw, RotateCcw } from "lucide-react";

import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { PortfolioPanel } from "@/components/portfolio/PortfolioPanel";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { apiBaseUrl } from "@/lib/apiDriver";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

const MOCK_SESSIONS = [
  { id: "sess_demo_001", title: "Cash decline — week of Jul 27", when: "now", active: true },
  { id: "sess_demo_002", title: "COB investigation — Silverline MA", when: "Jul 30", active: false },
  { id: "sess_demo_003", title: "Denial spike — Meridian Imaging", when: "Jul 22", active: false },
];

export function SessionRail({
  onReplay,
  replayDisabled,
}: {
  onReplay: () => void;
  replayDisabled: boolean;
}) {
  const simulateWatermarkRefresh = useSessionStore((s) => s.simulateWatermarkRefresh);
  const toggleFailurePreview = useSessionStore((s) => s.toggleFailurePreview);
  const showFailurePreview = useSessionStore((s) => s.showFailurePreview);
  const reset = useSessionStore((s) => s.reset);
  const turnCount = useSessionStore((s) => s.turns.length);
  const mode = useSessionStore((s) => s.connection.mode);

  return (
    <aside className="panel flex h-full min-h-0 flex-col border-r">
      <div className="flex items-center justify-between px-4 py-3.5">
        <div className="flex items-center gap-2">
          <span className="accent-gradient flex size-6 items-center justify-center rounded-md font-mono text-sm font-bold text-white">
            R
          </span>
          <span className="text-[0.9rem] font-semibold tracking-tight">Revi</span>
          <span className="mt-0.5 text-[0.55rem] font-medium uppercase tracking-widest text-muted-foreground">
            RCM
          </span>
        </div>
        <ThemeToggle />
      </div>

      <div className="px-3 pb-3">
        <Button
          onClick={onReplay}
          disabled={replayDisabled}
          size="sm"
          className="accent-gradient w-full gap-1.5 text-[0.72rem] font-medium text-white shadow-sm transition-all duration-150 hover:brightness-110 hover:shadow-md"
        >
          <Play className="size-3" />
          Replay reference demo
        </Button>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-5 px-3 pb-4">
          <section className="space-y-1">
            <h3 className="flex items-center gap-1.5 px-1 text-[0.68rem] font-semibold uppercase tracking-wide text-muted-foreground">
              <MessagesSquare className="size-3" />
              Sessions
            </h3>
            <ul className="space-y-0.5">
              {MOCK_SESSIONS.map((session) => (
                <li key={session.id}>
                  <button
                    type="button"
                    className={cn(
                      "flex w-full items-baseline justify-between gap-2 rounded-md px-2 py-1.5 text-left text-[0.7rem] transition-colors duration-150",
                      session.active
                        ? "bg-accent font-medium"
                        : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                    )}
                  >
                    <span className="truncate">{session.title}</span>
                    <span className="num shrink-0 text-[0.6rem] text-muted-foreground/70">
                      {session.when}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <Separator />
          <PortfolioPanel />
          <Separator />

          <section className="space-y-1.5">
            <h3 className="flex items-center gap-1.5 px-1 text-[0.68rem] font-semibold uppercase tracking-wide text-muted-foreground">
              <FlaskConical className="size-3" />
              Demo states
            </h3>
            <div className="space-y-1 px-1">
              <Button
                variant="outline"
                size="xs"
                className="w-full justify-start gap-1.5 text-[0.65rem] font-normal"
                onClick={simulateWatermarkRefresh}
              >
                <RefreshCw className="size-3" />
                Simulate data refresh (stale watermark)
              </Button>
              <Button
                variant="outline"
                size="xs"
                className={cn(
                  "w-full justify-start gap-1.5 text-[0.65rem] font-normal",
                  showFailurePreview && "border-negative/50 text-negative",
                )}
                onClick={toggleFailurePreview}
              >
                {showFailurePreview ? "Hide" : "Preview"} reconciliation failure
              </Button>
              <Button
                variant="outline"
                size="xs"
                className="w-full justify-start gap-1.5 text-[0.65rem] font-normal"
                disabled={turnCount === 0}
                onClick={reset}
              >
                <RotateCcw className="size-3" />
                Reset session
              </Button>
            </div>
          </section>
        </div>
      </ScrollArea>

      <div className="border-t px-4 py-2.5">
        <p className="num text-[0.58rem] leading-relaxed text-muted-foreground/70">
          {mode === "api" ? (
            <>
              Live API · {apiBaseUrl()}
              <br />
              M11 — real driver behind the mock&apos;s seam
            </>
          ) : (
            <>
              Mock data · seed 20260807 · snap_003
              <br />
              Set NEXT_PUBLIC_REVI_DRIVER=api for the live API
            </>
          )}
        </p>
      </div>
    </aside>
  );
}
