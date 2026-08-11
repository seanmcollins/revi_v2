"use client";

import { Telescope } from "lucide-react";
import { useState } from "react";

import { ResearchLaunchCard } from "@/components/research/ResearchLaunchCard";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { populationLabel, type ResearchOffer } from "@/lib/deepResearch";
import { cn } from "@/lib/utils";

/**
 * RUN DEEP RESEARCH — one control, wherever the platform offers the run,
 * and it OPENS THE CONFIRMATION rather than starting anything.
 *
 * The control appears in three places and is the same object in all of
 * them: beside a lead card's drill, on a worklist row inside an answer,
 * and on Home's still-catchable figure. One label everywhere, for the
 * reason the product already learned with "Monitor this" — two names for
 * one gesture is how a reader concludes they are two gestures.
 *
 * WHY IT IS A POPOVER AND NOT A LAUNCH. A run is a minute of work and a
 * real model call, so intent is confirmed first — and the confirmation is
 * worth showing rather than a yes/no dialog, because what a reader needs
 * in order to decide is WHAT will be analyzed and, once the wire carries
 * it, which populations are on offer. So the same `ResearchLaunchCard` the
 * composer path renders inline is what opens here, in its compact form:
 * one card, one Run button, one place for that copy to live.
 *
 * The form is BEHIND the button, exactly as `MonitorThis` puts its
 * sensitivity form behind its trigger: the common case is "this
 * population, go", and a reader who wants to change the scope opens the
 * card that offers it.
 *
 * PERSISTENT, NEVER HOVER-REVEALED. The rail's drill is allowed to be a
 * hover affordance because it repeats an action the card is already about;
 * this one is the only way to reach a surface that does not otherwise
 * exist, and a control that does not exist on a touch screen, in a
 * screenshot or on a projector is not a quiet control, it is an absent
 * one. Quiet is carried by the ink (solid muted, never an opacity step —
 * see `contrast.test.ts`), which warms under the pointer.
 */
export function RunDeepResearchButton({
  offer,
  size = "row",
  className,
}: {
  offer: ResearchOffer;
  /** `row` sits in a card's action bar; `inline` runs in a sentence. */
  size?: "row" | "inline";
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const population = populationLabel(offer.population);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="xs"
          data-research-offer={offer.population.kind}
          // WHAT THE PRESS DOES, said in the name. "Run deep research on
          // denials from Atlas Commercial" on a control that opens a
          // confirmation would promise a launch this button does not
          // perform — and a screen-reader user would be told a minute of
          // work had started when a card had opened.
          aria-label={`Deep research on ${population} — see what it will analyze`}
          className={cn(
            "h-5 shrink-0 gap-1 rounded-full px-1.5 text-micro font-normal",
            "text-muted-foreground transition-colors duration-150 hover:text-foreground",
            size === "inline" && "h-4 px-1",
            className,
          )}
        >
          <Telescope aria-hidden className="size-2.5" />
          {offer.label || "Run deep research"}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[24rem] max-w-[calc(100vw-2rem)] p-3">
        <ResearchLaunchCard offer={offer} variant="compact" onLaunched={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  );
}
