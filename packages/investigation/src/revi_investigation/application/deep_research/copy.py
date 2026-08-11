"""Every sentence a deep-research report says out loud, in one place.

Kept together rather than scattered through the builders for two reasons.
The report has to read as one document — the same fact worded two ways in
two sections is how a reader concludes they are two facts. And the client-
language guard walks this module directly, so a phrase that slips into our
vocabulary rather than the reader's fails a test instead of shipping.

The reader is an RCM analyst. They own denial, remit, payer, filing
deadline, resubmission, appeal. They do not own ours, and nothing here
asks them to.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

# ---------------------------------------------------------------------------
# formatting


def dollars(cents: int) -> str:
    """Whole cents as money a reader recognises."""
    value = Decimal(cents) / Decimal(100)
    return f"${value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}"


def dollar_value(cents: int) -> float:
    """Whole cents as the number a reader would say out loud.

    Findings publish money in DOLLARS, named as dollars. Handing a composer
    a bare integer of cents beside a field called "expected recoverable"
    invites the sentence "$243,984,144" over a figure that is $2,439,841.44
    — a hundredfold overstatement that every downstream check passes,
    because the digits are real and only the unit is wrong.
    """
    return float(Decimal(cents) / Decimal(100))


def percent(rate: Decimal | float, places: int = 1) -> str:
    value = (Decimal(str(rate)) * 100).quantize(
        Decimal("1") if places == 0 else Decimal("0." + "0" * places),
        rounding=ROUND_HALF_UP,
    )
    return f"{value}%"


def points(difference: Decimal | float, places: int = 1) -> str:
    """A rate difference, in percentage points."""
    value = (Decimal(str(difference)) * 100).quantize(
        Decimal("0." + "0" * places), rounding=ROUND_HALF_UP
    )
    unit = "point" if abs(value) == 1 else "points"
    return f"{value} percentage {unit}"


def count(n: int, singular: str, plural: str | None = None) -> str:
    """A count with its noun, pluralized rather than parenthesized."""
    word = singular if n == 1 else (plural or f"{singular}s")
    return f"{n:,} {word}"


# ---------------------------------------------------------------------------
# titles


TITLE_HEADLINE = "What the open denials are worth"
TITLE_STILL_CATCHABLE = "How much is still inside the filing deadline"
TITLE_NOT_ESTIMABLE = "What your own history cannot price yet"
TITLE_PAYER_GAP = "The gap between your strongest and weakest payer"
TITLE_CLASS_GAP = "The gap between your strongest and weakest denial type"
TITLE_TIMELINESS = "What waiting costs"
TITLE_DEADLINE = "What crossing the filing deadline costs"
TITLE_WORKED = "How much of this is being worked at all"
TITLE_RATES = "Recovery rate by population"


# ---------------------------------------------------------------------------
# the headline


def headline_statement(
    *,
    expected: int,
    low: int,
    high: int,
    open_dollars: int,
    open_denials: int,
) -> str:
    return (
        f"Working the {count(open_denials, 'open denial')} still outstanding "
        f"({dollars(open_dollars)} denied) should bring back about "
        f"{dollars(expected)}, somewhere between {dollars(low)} and {dollars(high)}."
    )


def split_statement(*, catchable: int, passed: int, unknown: int) -> str:
    parts = [
        f"{dollars(catchable)} is still inside the filing deadline",
        f"{dollars(passed)} is already past it",
    ]
    if unknown:
        parts.append(f"{dollars(unknown)} sits on plans with no filing limit on file")
    body = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])}, and {parts[-1]}"
    return f"Of the denied dollars still open, {body}."


def unpriced_statement(
    *, unpriced: int, share: Decimal, populations: int, no_deadline_rate: int = 0
) -> str:
    lead = (
        f"{dollars(unpriced)} — {percent(share)} of the open denied dollars — is left "
        f"out of the figure above: {dollars(unpriced - no_deadline_rate)} sits in "
        f"{count(populations, 'population')} your own history cannot price yet"
        if no_deadline_rate
        else (
            f"{dollars(unpriced)} — {percent(share)} of the open denied dollars — sits in "
            f"{count(populations, 'population')} your own history cannot price yet, and is "
            "left out of the figure above"
        )
    )
    if no_deadline_rate:
        return (
            f"{lead}, and {dollars(no_deadline_rate)} sits on a side of the filing "
            "deadline too few answered denials have reached for a rate to be published."
        )
    return f"{lead}."


def pricing_construction_statement(
    *,
    within_rate: Decimal | None,
    within_n: int,
    past_rate: Decimal | None,
    past_n: int,
    severity: Decimal,
    severity_wins: int,
) -> str:
    """How the headline was built, in one sentence a reader can check.

    Three moving parts, and the two that were missing are the two that
    made the old figure wrong: a denial past its filing deadline is not
    priced like one inside it, and a win is not worth the full denied
    amount. Both are stated with the evidence behind them so a reader can
    take the figure apart with the tables further down the page.
    """
    sides = []
    if within_rate is not None:
        sides.append(
            f"{percent(within_rate)} for what is still inside the deadline "
            f"(over {count(within_n, 'answered denial')})"
        )
    if past_rate is not None:
        sides.append(
            f"{percent(past_rate)} for what is past it "
            f"(over {count(past_n, 'answered denial')})"
        )
    rates = " and ".join(sides) if sides else "each population's own measured rate"
    return (
        "That figure is built one population at a time: its open dollars are split by "
        f"whether the filing deadline has passed, each side is priced at the rate "
        f"denials on that side actually come back at — {rates} — and the result is "
        f"multiplied by {cents_on_the_dollar(severity)} on the dollar, which is what a "
        f"win has actually returned across {count(severity_wins, 'recovered denial')}."
    )


def cents_on_the_dollar(ratio: Decimal) -> str:
    """A severity ratio as money, because that is what it is."""
    value = (Decimal(str(ratio)) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{value} cents"


def severity_statement(*, ratio: Decimal, wins: int, recovered: int, denied: int) -> str:
    return (
        f"A win is not the full denied amount. Across the {count(wins, 'denial')} that "
        f"came back, payers allowed {dollars(recovered)} of {dollars(denied)} denied — "
        f"{cents_on_the_dollar(ratio)} on the dollar. Every figure above is multiplied "
        "by that."
    )


def coarsened_statement(*, ran: tuple[str, ...], dropped: tuple[str, ...]) -> str:
    """Said when the cut that ran is not the cut that was planned.

    A pricing total depends on how finely it was cut, so a run that
    silently priced a coarser population than the plan named would publish
    a bigger, blander number over a table the reader would read as the
    finer one.
    """
    ran_words = " and ".join(ran) if ran else "the whole population"
    dropped_words = " and ".join(dropped)
    return (
        f"The plan asked to price by {ran_words} and {dropped_words}. "
        f"{dropped_words.capitalize()} has no ranges set in your definitions, so this "
        f"was priced by {ran_words} instead."
    )


def pursuit_transfer_statement(
    *,
    worked_share: Decimal,
    open_share: Decimal,
    weakest: tuple[str, ...],
) -> str:
    """The selection effect, stated as a first-class methods note.

    The rate is measured on denials somebody chose to work and the payer
    then answered. Staff work what wins, so the answered set is not a
    random sample of the open inventory — and the open inventory is
    heavier in exactly the kinds of denial that win least. That is a
    transfer this report is making on purpose, and the reader is entitled
    to the size of it.
    """
    names = " and ".join(weakest)
    return (
        f"These rates are measured on denials that were worked and then answered, and "
        f"they are being applied to denials that have not been worked. Those are not "
        f"the same mix: {names} — the kinds that come back least often — are "
        f"{percent(open_share)} of the open denied dollars but only "
        f"{percent(worked_share)} of the answered denials the rates come from. Pricing "
        "each kind of denial at its own rate is what keeps that from inflating the "
        "total, which is why the money above is cut that way."
    )


def priced_by_statement(labels: tuple[str, ...]) -> str:
    """Which cut the money was priced at.

    A coarser cut prices more of the inventory and blends more together; a
    finer one prices less and blends less. Both are honest and they give
    different totals, so the total says which one produced it rather than
    leaving a reader to infer it from the size of the table below.
    """
    if not labels:
        return "Priced over the whole population at its own measured rate."
    cut = labels[0] if len(labels) == 1 else f"{' and '.join(labels[:-1])} and {labels[-1]}"
    return (
        f"Priced by {cut}: each population's open denials at that population's own "
        "measured rate."
    )


NO_PRIOR_SUBSTITUTED = (
    "Every rate here is measured on your own denials. Nothing is filled in from an "
    "industry average."
)

INDEPENDENCE_CAVEAT = (
    "The range around the total is the sum of each population's own range — the widest "
    "way to add ranges up, and wider than it would be if the populations moved "
    "independently. Read it as a spread rather than a guarantee."
)

AMOUNTS_KNOWN_CAVEAT = (
    "The range moves with the recovery rate only. The denied amounts, and the share of "
    "a denied dollar a win returns, are treated as known — so the true spread is wider "
    "than the one shown."
)

DEADLINE_UNKNOWN_NOTE = (
    "Some plans carry no filing limit on file. That is not evidence the claim is "
    "still filable, so those dollars are shown on their own line."
)

THIN_LABEL = "not estimable from your data yet"

THIN_EXPLANATION = (
    "These populations are counted and their denied dollars are shown, but no rate "
    "is published for them and they are left out of the total."
)


def thin_rollup_statement(*, populations: int, denials: int, cents: int, floor: int) -> str:
    return (
        f"A further {count(populations, 'population')} hold "
        f"{count(denials, 'open denial')} worth {dollars(cents)} between them. Each is "
        f"smaller than {floor} denials, so they are shown together rather than named."
    )


# ---------------------------------------------------------------------------
# contrasts


def contrast_statement(
    *,
    subject: str,
    strong_label: str,
    strong_rate: Decimal,
    strong_n: int,
    weak_label: str,
    weak_rate: Decimal,
    weak_n: int,
    difference: Decimal,
) -> str:
    lead = strong_label[:1].upper() + strong_label[1:] if strong_label else strong_label
    return (
        f"{lead} gives back {percent(strong_rate)} of what you resubmit "
        f"(over {count(strong_n, 'answered denial')}); {weak_label} gives back "
        f"{percent(weak_rate)} (over {count(weak_n, 'answered denial')}). "
        f"That is a gap of {points(difference)}."
    )


def separation_statement(*, p_value: Decimal, low: Decimal, high: Decimal) -> str:
    if p_value < Decimal("0.001"):
        chance = "under 1 in 1,000"
    elif p_value < Decimal("0.01"):
        chance = "under 1 in 100"
    elif p_value < Decimal("0.05"):
        chance = "under 1 in 20"
    else:
        return (
            "A gap this size appears often enough by chance that these two "
            f"populations cannot be called different on this evidence — the gap could "
            f"be anywhere from {points(low)} to {points(high)}."
        )
    return (
        f"The chance of seeing a gap this size if the two were really the same is "
        f"{chance}. The gap itself is somewhere between {points(low)} and {points(high)}."
    )


EXTREMES_CAVEAT = (
    "These two were picked because they sit at the ends of the range, so read the gap "
    "as a place to look rather than a result that has been confirmed."
)


def held_inside_statement(*, label: str, population: str) -> str:
    """Which population a comparison was held inside, and why that one.

    A comparison run inside one kind of denial is a different claim from the
    same comparison run over everything, and the difference is exactly the
    mix it removed. Left unsaid, a within-type gap reads as a whole-payer
    gap — which is the confound the cut existed to avoid, reintroduced in
    the sentence.
    """
    return (
        f"Compared inside {label} only — the {population.lower()} with the most "
        "answered denials — so the gap is not a mix of different kinds of denial."
    )


def contrast_refused_statement(*, subject: str, floor_sentence: str) -> str:
    return (
        f"Fewer than two {subject.lower()} populations have enough answered denials "
        f"to compare. {floor_sentence}"
    )


# ---------------------------------------------------------------------------
# timeliness and the deadline


def timeliness_statement(
    *,
    fast_band: str,
    fast_rate: Decimal,
    slow_band: str,
    slow_rate: Decimal,
) -> str:
    return (
        f"Denials resubmitted within {fast_band} days come back "
        f"{percent(fast_rate)} of the time. Once the wait reaches {slow_band} days it "
        f"is {percent(slow_rate)}."
    )


def timeliness_implication(*, fast_band: str, drop: Decimal, within: str = "") -> str:
    """What speed is worth — and, unstratified, why that is an upper bound.

    The estimator behind this curve says so itself: the slow bands hold the
    slow *kinds* of denial, which are also the weak ones, so an
    unstratified slope mixes the timing effect with the mix and overstates
    it. Shipping the number without that sentence turns a descriptive curve
    into an operational promise nobody measured.
    """
    lead = (
        f"Getting the work out inside {fast_band} days is worth {points(drop)} on the "
        "recovery rate compared with the slowest group."
    )
    if within:
        return f"{lead} Read inside {within} only, so the gap is not a mix of different kinds."
    return (
        f"{lead} That is not all speed: the slowest group also holds the kinds of "
        "denial that come back least often, so read it as the most speed could be "
        "worth rather than what it is worth."
    )


def median_delay_statement(*, label: str, median: Decimal) -> str:
    days = median.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{label} denials currently take a median of {days} days to go back out."


def deadline_statement(
    *,
    within_rate: Decimal,
    within_n: int,
    past_rate: Decimal,
    past_n: int,
) -> str:
    return (
        f"Resubmitted inside the filing deadline, {percent(within_rate)} of denials "
        f"come back (over {count(within_n, 'answered denial')}). Past it, "
        f"{percent(past_rate)} do (over {count(past_n, 'answered denial')})."
    )


def deadline_side_refused_statement(*, side: str, n: int, floor_sentence: str) -> str:
    """Said when one side of the deadline is too thin to read.

    The comparison is the point of the section, so half of it missing is a
    fact the reader needs — not a sentence that quietly does not appear.
    """
    return (
        f"What happens {side.lower()} cannot be read here: only "
        f"{count(n, 'denial')} on that side have an answer from the payer. "
        f"{floor_sentence}"
    )


DEADLINE_AUTHORITY_NOTE = (
    "The drop is steeper where the filing limit is confirmed than where it is still a "
    "planning default, so the two are shown apart. Treating every limit as confirmed "
    "overstates the cliff."
)


def zero_rate_bound_statement(*, high: Decimal, n: int) -> str:
    return (
        f"None of the {count(n, 'answered denial')} filed past a confirmed deadline "
        f"came back. That is not the same as never: on this many denials the true rate "
        f"could still be as high as {percent(high)}."
    )


# ---------------------------------------------------------------------------
# what the data edge cost


def censoring_statements(
    *,
    considered: int,
    in_denominator: int,
    open_undecided: int,
    not_pursued: int,
    immature: int,
    data_edge: str,
) -> tuple[str, ...]:
    lines = [
        f"Rates here are measured over the {count(in_denominator, 'denial')} the payer "
        f"has already answered, out of {count(considered, 'denial')} in this population."
    ]
    if open_undecided:
        lines.append(
            f"{count(open_undecided, 'denial')} have been resubmitted and are still "
            "waiting on the payer. They are counted in neither the wins nor the losses."
        )
    if not_pursued:
        lines.append(
            f"{count(not_pursued, 'denial')} have no resubmission on file. Some of "
            "those have not been worked yet rather than never; nothing in the data "
            "separates the two."
        )
    if immature:
        lines.append(
            f"{count(immature, 'denial')} are too recent for their silence to mean "
            "anything yet, so they are left out rather than counted as unworked."
        )
    lines.append(f"Everything above is as the data stood on {data_edge}.")
    return tuple(lines)


NAIVE_DENOMINATOR_NOTE = (
    "Dividing recoveries by every denial instead would charge each open story as a "
    "loss and understate what this work is worth."
)


def maturity_windows_statement(*, windows: tuple[tuple[str, int], ...]) -> str:
    """The wait, per kind of denial, before silence means anything.

    These are a claim about how this organisation works denials, not
    something the data can be asked. Printing them is the only way a reader
    can disagree with them.
    """
    if not windows:
        return (
            "No waiting period is set for any kind of denial, so how often denials are "
            "worked at all cannot be read here."
        )
    parts = ", ".join(f"{label} {days} days" for label, days in windows)
    return (
        f"How often denials are worked is read only over denials old enough that a "
        f"resubmission would have gone out by now: {parts}. Younger denials are left "
        "out rather than counted as unworked. You can change these anytime."
    )


DECIDED_MATURITY_NOTE = (
    "That waiting period does not apply to the recovery rate itself. A recovery rate "
    "is measured only over denials the payer has already answered, so a denial that is "
    "too recent to have an answer is simply not in it — there is nothing to exclude, "
    "and nothing is."
)


def decided_exclusions_statement(*, open_undecided: int, not_pursued: int, total: int) -> str:
    """What the answered-only denominator left behind, in counts."""
    return (
        f"{count(open_undecided + not_pursued, 'denial')} of the "
        f"{count(total, 'denial')} in this population are not in any rate above: "
        f"{count(open_undecided, 'denial')} resubmitted and still waiting, and "
        f"{count(not_pursued, 'denial')} with no resubmission on file."
    )


# ---------------------------------------------------------------------------
# pursuit


def pursuit_statement(*, label: str, rate: Decimal, n: int) -> str:
    return (
        f"{label} denials are worked {percent(rate)} of the time, over the "
        f"{count(n, 'denial')} old enough to tell."
    )


# ---------------------------------------------------------------------------
# refusals and run-level notes


def angle_refused_statement(*, title: str, reason: str) -> str:
    return f"{title} was not run: {reason}"


def population_label(kind: str, values: tuple[str, ...]) -> str:
    """The one sentence naming which denials a run covered."""
    if kind == "payer" and values:
        return f"denials from {', '.join(values)}"
    if kind == "recovery_class" and values:
        return f"{', '.join(values).lower()} denials"
    if kind == "facility" and values:
        return f"denials at {', '.join(values)}"
    return "every open denial"


def data_load_label(newest_data_date: str) -> str:
    return f"the load through {newest_data_date}"


def header_display(*, population: str, floor_sentence: str, load: str) -> str:
    return f"{population.capitalize()}, on the service date, from {load}. {floor_sentence}"
