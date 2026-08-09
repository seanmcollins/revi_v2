"use client";

import { Check, Flag, Wrench } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useSessionStore, type FeedbackChoice } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * Three buttons whose labels used to describe a workflow that does not
 * exist. Nothing receives this feedback: `setFeedback` writes to the
 * client store and the store is not persisted, so "Request review" and
 * "Flagged for analyst review with the full evidence trail attached"
 * described a queue, a reviewer and an attachment, none of which are
 * real. The labels below say exactly what happens.
 */
const CHOICES: Array<{ id: FeedbackChoice; label: string; icon: React.ReactNode }> = [
  { id: "yes", label: "Yes", icon: <Check className="size-3" /> },
  { id: "fix", label: "Needs work", icon: <Wrench className="size-3" /> },
  { id: "review", label: "Flag it", icon: <Flag className="size-3" /> },
];

const CLOSURE: Record<FeedbackChoice, string> = {
  yes: "Noted for this session only — nothing is sent anywhere.",
  fix: "Noted for this session only — nothing is sent anywhere, and nothing auto-learns from it.",
  review: "Noted for this session only — nothing is sent anywhere.",
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
