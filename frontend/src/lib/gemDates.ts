/**
 * GeM dates (listing card and bid PDF) are India time and come from the API without a timezone,
 * e.g. "2026-11-16T17:00:00". Read them as IST so "days left" is right in any browser.
 */
import { Tender } from "@/types/tender-agent";

const HAS_ZONE = /(Z|[+-]\d{2}:?\d{2})$/;

export const parseGemDate = (value?: string | null): Date | null => {
  if (!value) return null;
  const text = String(value).replace(" ", "T");
  const date = new Date(HAS_ZONE.test(text) ? text : `${text}+05:30`);
  return Number.isNaN(date.getTime()) ? null : date;
};

/** "16 Nov 2026" (or "16 Nov 2026, 5:00 pm" with time) in India time. */
export const formatGemDate = (value?: string | null, withTime = false): string | null => {
  const date = parseGemDate(value);
  if (!date) return null;
  return date.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
    ...(withTime ? { hour: "numeric", minute: "2-digit" } : {}),
  });
};

export const daysLeft = (value?: string | null): number | null => {
  const date = parseGemDate(value);
  if (!date) return null;
  return Math.max(0, Math.floor((date.getTime() - Date.now()) / 86_400_000));
};

/** The bid PDF's original closing date when the buyer has since extended the deadline. */
export const extendedFrom = (tender?: Tender): string | null => {
  const card = parseGemDate(tender?.scraped_info?.end_date);
  const pdf = parseGemDate(tender?.metadata?.pdf_bid_end_date);
  if (!card || !pdf || card.getTime() - pdf.getTime() < 60_000) return null;
  return tender?.metadata?.pdf_bid_end_date || null;
};
