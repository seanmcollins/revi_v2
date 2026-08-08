"use client";

import { Check, Flag, Wrench } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useSessionStore, type FeedbackChoice } from "@/lib/store";
import { cn } from "@/lib/utils";

const CHOICES: Array<{ id: FeedbackChoice; label: string; icon: React.ReactNode }> = [
  { id: "yes", label: "Yes", icon: <Check className="size-3" /> },
  { id: "fix", label: "Fix it", icon: <Wrench className="size-3" /> },
  { id: "review", label: "Request review", icon: <Flag className="size-3" /> },
];

const CLOSURE: Record<FeedbackChoice, string> = {
  yes: "Logged. Thanks — recorded against this trace.",
  fix: "Logged to the trace for pack review. Improvements are human-gated — nothing auto-learns from this.",
  review: "Flagged for analyst review with the full evidence trail attached.",
};

/** Per-answer triage, persisted to the store (traces later). */
export function FeedbackTriage({ turnId }: { turnId: string }) {
  const choice = useSessionStore((s) => s.feedback[turnId]);
  const setFeedback = useSessionStore((s) => s.setFeedback);

  return (
    <div className="flex min-h-6 flex-wrap items-center gap-2">
      <span className="text-[0.65rem] text-muted-foreground">Did this answer it?</span>
      <div className="flex gap-1">
        {CHOICES.map((c) => (
          <Button
            key={c.id}
            variant={choice === c.id ? "secondary" : "ghost"}
            size="xs"
            className={cn(
              "h-5 gap-1 rounded-full px-2 text-[0.65rem] font-normal text-muted-foreground",
              choice === c.id && "font-medium text-foreground",
            )}
            onClick={() => setFeedback(turnId, c.id)}
          >
            {c.icon}
            {c.label}
          </Button>
        ))}
      </div>
      {choice && (
        <span className="text-[0.62rem] italic text-muted-foreground">{CLOSURE[choice]}</span>
      )}
    </div>
  );
}
