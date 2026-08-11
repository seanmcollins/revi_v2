import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";

import { MONITORS_ANCHOR } from "@/lib/monitorsAnchor";

/**
 * NOT FOUND — because the alternative was a blank page.
 *
 * The route table had four paths and no catch-all, so anything that did
 * not match one of them resolved to nothing: `RootLayout` mounted its
 * providers, `Outlet` rendered null, and the browser drew an empty page on
 * the app's own background. The owner found it the way anyone would — by
 * following `/rounds`, the address this surface had before it was renamed
 * to Monitors, still sitting in browser autocomplete — and read the blank
 * page as the app being broken, which is exactly what a blank page says.
 *
 * `/rounds` and `/monitors` are both redirects now (see `App.tsx`), so
 * those two addresses land on Home. This is the floor under every OTHER
 * mistyped, stale or invented path: something rather than nothing, in the
 * product's own register.
 *
 * THE REGISTER IS THE POINT. This page is not a verdict — nothing was
 * measured, nothing was refused, no premise was corrected — so it carries
 * none of the verdict grammar: no amber, no alert role, no warning mark.
 * It is a quiet card, one sentence, and two links to places that exist.
 * The sentence says "nothing is broken" because on this page that is the
 * single most useful true thing there is to say.
 *
 * It renders no rail and no header on purpose: both are surfaces' chrome,
 * built on live reads, and a not-found page that has to fetch before it can
 * tell you it found nothing would be its own small joke.
 */
export function NotFound() {
  return (
    <div className="relative h-dvh overflow-hidden bg-background">
      <div aria-hidden className="page-glow pointer-events-none absolute inset-0" />

      <main
        aria-labelledby="not-found-heading"
        className="relative h-full overflow-y-auto px-6 py-16"
      >
        <div className="raised mx-auto max-w-xl space-y-3 rounded-xl border bg-surface-raised p-5">
          <h1 id="not-found-heading" className="text-lead font-semibold tracking-tight">
            This page doesn&apos;t exist.
          </h1>

          <p className="text-body leading-relaxed text-muted-foreground">
            The address did not match anything this app serves, so there is nothing to show
            here — nothing is broken.
          </p>

          <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-meta text-muted-foreground">
            <Link
              to="/"
              className="focus-ring inline-flex items-center gap-1 rounded underline decoration-foreground/30 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
            >
              Home
              <ArrowRight aria-hidden className="size-2.5" />
            </Link>
            {/* The second destination is a ZONE now rather than a route:
                `/monitors` was retired into Home's digest, so the honest
                offer from a not-found page is the anchor that lands on it.
                A cold load at `/#home-monitors` focuses the zone — see
                `Home`, which reads the hash on arrival. */}
            <Link
              to={MONITORS_ANCHOR}
              className="focus-ring inline-flex items-center gap-1 rounded underline decoration-foreground/30 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
            >
              Your monitors
              <ArrowRight aria-hidden className="size-2.5" />
            </Link>
          </p>
        </div>
      </main>
    </div>
  );
}
