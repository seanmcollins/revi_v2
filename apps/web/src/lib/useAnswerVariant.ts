"use client";

import { useSyncExternalStore } from "react";

import {
  currentAnswerVariant,
  serverAnswerVariant,
  subscribeAnswerVariant,
  type AnswerVariant,
} from "@/lib/answerVariant";

/**
 * The answer layout this browser is on, hydration-safe.
 *
 * The server has no URL parameter and no localStorage, so its snapshot is
 * the default layout; the client's reads both. This is the same shape the
 * workspace already uses for driver selection — a `useSyncExternalStore`
 * pair rather than an effect, so the first client render agrees with the
 * server render and nothing flashes one layout before settling on another.
 */
export function useAnswerVariant(): AnswerVariant {
  return useSyncExternalStore(
    subscribeAnswerVariant,
    currentAnswerVariant,
    serverAnswerVariant,
  );
}
