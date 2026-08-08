"use client";

import { useEffect, useMemo, useState } from "react";

import { ChatThread } from "@/components/chat/ChatThread";
import { TurnInput } from "@/components/chat/TurnInput";
import { ContextPanel } from "@/components/workspace/ContextPanel";
import { SessionRail } from "@/components/workspace/SessionRail";
import { mediumDate } from "@/lib/format";
import { GOLDEN_QUESTIONS, MockDriver } from "@/lib/mockDriver";
import { useSessionStore } from "@/lib/store";

/**
 * The Revi workspace: left rail (sessions + portfolio), center thread,
 * right contextual panel (evidence + lineage). Desktop tool — designed
 * down to 1280px.
 */
export default function Workspace() {
  const driver = useMemo(() => new MockDriver(), []);
  const setDriver = useSessionStore((s) => s.setDriver);
  const submit = useSessionStore((s) => s.submit);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const turns = useSessionStore((s) => s.turns);
  const watermark = useSessionStore((s) => s.watermark);
  const [replaying, setReplaying] = useState(false);

  useEffect(() => {
    setDriver(driver);
  }, [driver, setDriver]);

  const answeredGolden = turns.filter((t) =>
    GOLDEN_QUESTIONS.includes(t.submission.utterance ?? ""),
  ).length;

  const replay = async () => {
    if (replaying || streaming) return;
    setReplaying(true);
    try {
      for (const question of GOLDEN_QUESTIONS.slice(answeredGolden)) {
        await submit({ utterance: question });
      }
    } finally {
      setReplaying(false);
    }
  };

  const suggestions =
    answeredGolden < GOLDEN_QUESTIONS.length
      ? [GOLDEN_QUESTIONS[answeredGolden], ...(turns.length === 0 ? ["What is PR3?"] : [])]
      : ["What is PR3?"];

  return (
    <div className="grid h-dvh grid-cols-[16.5rem_minmax(0,1fr)_21rem] min-[1440px]:grid-cols-[17.5rem_minmax(0,1fr)_23rem]">
      <SessionRail onReplay={() => void replay()} replayDisabled={replaying || streaming} />

      <main className="flex h-full min-h-0 flex-col">
        <header className="flex shrink-0 items-center justify-between border-b px-6 py-2.5">
          <div>
            <h1 className="text-[0.85rem] font-semibold tracking-tight">
              Cash decline — week of Jul 27
            </h1>
            <p className="num text-[0.62rem] text-muted-foreground">
              Session pinned · watermark {watermark.loadedAt} · data through{" "}
              {mediumDate(watermark.newestDataDate)} · base-rcm@1.0.0
            </p>
          </div>
          <p className="num text-[0.62rem] text-muted-foreground">
            {turns.length} turn{turns.length === 1 ? "" : "s"}
          </p>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <ChatThread />
        </div>

        <footer className="shrink-0 border-t px-6 py-3">
          <div className="mx-auto max-w-3xl">
            <TurnInput suggestions={suggestions} />
          </div>
        </footer>
      </main>

      <ContextPanel />
    </div>
  );
}
