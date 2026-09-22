import type { Brief, Stance } from "../types/models";

/** Loom's vocabulary, translated for someone who does not work in finance.
 *
 *  The engine's own words are precise and useless to a beginner. "Strong
 *  negative stance, confidence 0.84" is exact and tells a first-time investor
 *  nothing they can act on; "the evidence is clearly negative, and Loom is
 *  fairly sure" says the same thing and can be read at a glance.
 *
 *  Nothing here softens a verdict. A beginner protected from the word
 *  "negative" is a beginner being misled, and the whole point of the simple
 *  view is that it carries the same judgement in different words. */

export interface PlainVerdict {
  label: string;
  meaning: string;
  tone: "positive" | "negative" | "mixed" | "quiet";
}

const VERDICTS: Record<Stance, PlainVerdict> = {
  strong_negative: {
    label: "Clearly negative",
    meaning: "Several serious problems, with little pointing the other way.",
    tone: "negative",
  },
  negative: {
    label: "Leaning negative",
    meaning: "More concerns than positives in what the company has disclosed.",
    tone: "negative",
  },
  mixed: {
    label: "Genuinely mixed",
    meaning: "Real arguments on both sides. Not a shrug, a disagreement.",
    tone: "mixed",
  },
  positive: {
    label: "Leaning positive",
    meaning: "More positives than concerns in what the company has disclosed.",
    tone: "positive",
  },
  strong_positive: {
    label: "Clearly positive",
    meaning: "Several strong positives, with little pointing the other way.",
    tone: "positive",
  },
  quiet: {
    label: "Quiet",
    meaning: "Loom has read the filings and found nothing pointing either way.",
    tone: "quiet",
  },
  insufficient: {
    label: "Not enough read yet",
    meaning: "Loom has not analysed enough about this company to have a view.",
    tone: "quiet",
  },
};

export function verdictOf(stance: Stance): PlainVerdict {
  return VERDICTS[stance] ?? VERDICTS.insufficient;
}

/** Confidence as a phrase. A number between 0 and 1 invites false precision:
 *  0.84 and 0.79 are not meaningfully different, and printing both as decimals
 *  implies they are. */
export function confidencePhrase(confidence: number): string {
  if (confidence >= 0.8) return "Loom is fairly sure";
  if (confidence >= 0.6) return "Loom is moderately sure";
  if (confidence >= 0.4) return "Loom is unsure";
  return "Loom is barely sure at all";
}

/** How many independent kinds of source agree. The most useful honesty signal
 *  there is: one filing saying something is far weaker than a filing, a
 *  transcript and an insider trade saying it. */
export function evidenceBreadth(brief: Brief): string {
  const count = brief.source_labels?.length ?? 0;
  if (count >= 4) return `${count} different kinds of source agree`;
  if (count === 3) return "three different kinds of source agree";
  if (count === 2) return "two different kinds of source agree";
  if (count === 1) return "only one kind of source so far";
  return "no sources recorded";
}

/** Below this share of a company's own findings, a severity is unusual for it.
 *  Mirrors ANOMALY_RATE in the backend's statistics engine; the backend makes
 *  the decision, this exists so the interface can describe it consistently. */
const UNUSUAL_RATE = 0.1;

export function isUnusualForCompany(
  rate: number | null | undefined,
): boolean {
  return rate !== null && rate !== undefined && rate < UNUSUAL_RATE;
}

/** Why a finding is worth more attention than its label alone suggests.
 *
 *  Severity as extracted is absolute, and absolute severity is a poor guide to
 *  what matters: a company that files a "major" risk every quarter is telling
 *  you about its disclosure habits, not about a change in its business. The
 *  same label from a company that has never used it is telling you something
 *  happened. That distinction is invisible in a feed and is exactly what this
 *  sentence restores.
 *
 *  Returns null when there is no baseline. An unscored finding is not an
 *  ordinary one, and saying nothing is honest where saying "typical" would not
 *  be. */
export function rarityPhrase(
  rate: number | null | undefined,
  sampleSize: number | null | undefined,
): string | null {
  if (!isUnusualForCompany(rate)) return null;
  const percent = Math.round((rate as number) * 100);
  const shown = percent < 1 ? "under 1%" : `${percent}%`;
  const basis = sampleSize ? ` of its last ${sampleSize} findings` : " of its findings";
  return `Unusual for this company: only ${shown}${basis} are this serious.`;
}

export function daysUntilPhrase(days: number | null): string | null {
  if (days === null) return null;
  if (days === 0) return "reports today";
  if (days === 1) return "reports tomorrow";
  if (days <= 7) return `reports in ${days} days`;
  if (days <= 30) return `reports in ${Math.round(days / 7)} weeks`;
  return null;
}

export function moneyShort(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(1)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(0)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

export function toneClass(tone: PlainVerdict["tone"]): string {
  if (tone === "positive") return "value-positive";
  if (tone === "negative") return "value-negative";
  if (tone === "mixed") return "value-neutral";
  return "faint";
}
