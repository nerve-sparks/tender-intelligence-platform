import type { ReactNode } from "react";
import { CalendarClock, CheckCircle2, HelpCircle, ClipboardList, ShieldCheck, Target, XCircle } from "lucide-react";

import { Level, Recommendation, Tender, TenderMatch } from "@/types/tender-agent";

const levelStyle: Record<Level, string> = {
  HIGH: "bg-[#daf4eb] text-[#16835e]",
  MEDIUM: "bg-[#fdeecf] text-[#a46a00]",
  LOW: "bg-[#fde4d6] text-[#c25a1c]",
  NONE: "bg-[#fddcdb] text-[#d35353]",
};

const recommendationStyle: Record<Recommendation, string> = {
  PURSUE: "bg-[#16835e] text-white",
  CONSIDER: "bg-[#c98300] text-white",
  SKIP: "bg-[#d35353] text-white",
};

const recommendationLabel: Record<Recommendation, string> = {
  PURSUE: "Pursue",
  CONSIDER: "Consider",
  SKIP: "Do not pursue",
};

export const procurementLabel = (value?: string | null) => {
  if (!value) return null;
  const labels: Record<string, string> = {
    goods: "Goods",
    services: "Services",
    works: "Works",
    software: "Software / IT",
    consulting: "Consulting",
    mixed: "Mixed",
  };
  return labels[value.toLowerCase()] || value;
};

export const RecommendationBadge = ({ match }: { match?: TenderMatch }) => {
  if (!match?.recommendation) {
    return (
      <span className="rounded-full border border-[#d6dae8] px-2 py-0.5 text-[11px] text-[#646d84]">
        Relevance check pending
      </span>
    );
  }
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${recommendationStyle[match.recommendation]}`}>
      {recommendationLabel[match.recommendation]}
    </span>
  );
};

const LevelPill = ({ label, value }: { label: string; value?: Level | null }) => {
  if (!value) return null;
  return (
    <span className={`rounded px-2 py-0.5 text-[11px] ${levelStyle[value]}`}>
      {label}: {value}
    </span>
  );
};

export const MatchLevels = ({ match }: { match?: TenderMatch }) => {
  if (!match) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      <LevelPill label="Business" value={match.business_relevance} />
      <LevelPill label="Capability" value={match.capability_alignment} />
      <LevelPill label="Buyer" value={match.buyer_relevance} />
      {match.eligibility && match.eligibility.fit !== "UNKNOWN" && (
        <span
          className={`rounded px-2 py-0.5 text-[11px] ${
            match.eligibility.fit === "MET" ? levelStyle.HIGH : levelStyle.NONE
          }`}
        >
          Eligibility: {match.eligibility.fit === "MET" ? "MET" : "NOT MET"}
        </span>
      )}
    </div>
  );
};

const Section = ({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) => (
  <section className="rounded-lg border border-[#ebedf4] p-4">
    <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-[#222835]">
      {icon}
      {title}
    </h3>
    {children}
  </section>
);

const Bullets = ({ items }: { items?: string[] }) =>
  items && items.length ? (
    <ul className="list-disc space-y-1 pl-5 text-sm text-[#475068]">
      {items.map((item, index) => (
        <li key={`${item}-${index}`}>{item}</li>
      ))}
    </ul>
  ) : null;

/** Company-specific verdict: why it matches, gaps, and bidder criteria vs the profile. */
export const MatchVerdict = ({ match }: { match?: TenderMatch }) => {
  if (!match) return null;
  const checks = match.eligibility?.checks || [];
  return (
    <Section icon={<Target size={16} className="text-[#4040E0]" />} title="Fit for your company">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-2xl font-semibold text-[#202532]">{Math.round(match.score * 100)}%</span>
        <RecommendationBadge match={match} />
        <MatchLevels match={match} />
      </div>
      {match.reason && <p className="mb-3 text-sm text-[#475068]">{match.reason}</p>}
      <div className="grid gap-4 md:grid-cols-2">
        {match.why_matches && match.why_matches.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-semibold uppercase text-[#16835e]">Why it matches</p>
            <Bullets items={match.why_matches} />
          </div>
        )}
        {match.gaps && match.gaps.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-semibold uppercase text-[#d35353]">Potential gaps</p>
            <Bullets items={match.gaps} />
          </div>
        )}
      </div>
      {checks.length > 0 && (
        <div className="mt-3 space-y-1">
          <p className="text-xs font-semibold uppercase text-[#646d84]">Bidder criteria vs your profile</p>
          {checks.map((check) => (
            <div key={check.label} className="flex items-center gap-2 text-sm text-[#475068]">
              {check.status === "met" && <CheckCircle2 size={14} className="text-[#16835e]" />}
              {check.status === "not_met" && <XCircle size={14} className="text-[#d35353]" />}
              {check.status === "unknown" && <HelpCircle size={14} className="text-[#8a90a3]" />}
              {check.status === "exempt" && <ShieldCheck size={14} className="text-[#16835e]" />}
              <span>
                {check.label}: required {check.required}, you have {check.company}
              </span>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
};

/** Company-independent brief of what the tender is buying (scope, schedule, criteria). */
export const TenderBriefSections = ({ tender }: { tender?: Tender }) => {
  const meta = tender?.metadata;
  if (!meta) return null;
  const dates = meta.key_dates || {};
  const schedule = [
    { label: "Pre-bid queries", value: dates.pre_bid_queries },
    { label: "Pre-bid meeting", value: dates.pre_bid_meeting },
    { label: "Bid submission", value: dates.bid_submission || (tender?.scraped_info?.end_date ? String(tender.scraped_info.end_date).slice(0, 16).replace("T", " ") : null) },
    { label: "Bid opening", value: dates.bid_opening },
  ].filter((entry) => entry.value);

  const criteria = [
    meta.min_avg_turnover ? `Minimum average annual turnover: ${meta.min_avg_turnover}` : null,
    meta.min_experience_years ? `Past experience: ${meta.min_experience_years} years` : null,
    meta.similar_work_required ? `Similar work: ${meta.similar_work_required}` : null,
    ...(meta.eligibility_requirements || []),
    ...(meta.preferences || []),
    ...(meta.evaluation || []),
  ].filter(Boolean) as string[];

  const hasScope = (meta.scope_categories && meta.scope_categories.length > 0) || (meta.scope_items && meta.scope_items.length > 0);

  return (
    <div className="grid gap-4">
      {(meta.what_is_being_bought || hasScope) && (
        <Section icon={<ClipboardList size={16} className="text-[#4040E0]" />} title="Brief scope of work">
          {meta.what_is_being_bought && <p className="mb-2 text-sm text-[#2c3345]">{meta.what_is_being_bought}</p>}
          {meta.scope_categories && meta.scope_categories.length > 0 ? (
            <div className="grid gap-3 md:grid-cols-2">
              {meta.scope_categories.map((category) => (
                <div key={category.name} className="rounded-md bg-[#f6f7fb] p-3">
                  <p className="mb-1 text-sm font-semibold text-[#222835]">{category.name}</p>
                  <Bullets items={category.items} />
                </div>
              ))}
            </div>
          ) : (
            <Bullets items={meta.scope_items} />
          )}
        </Section>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {schedule.length > 0 && (
          <Section icon={<CalendarClock size={16} className="text-[#4040E0]" />} title="Bid schedule">
            <ul className="space-y-1 text-sm text-[#475068]">
              {schedule.map((entry) => (
                <li key={entry.label}>
                  <span className="font-medium text-[#2c3345]">{entry.label}:</span> {entry.value}
                </li>
              ))}
            </ul>
          </Section>
        )}
        {(meta.emd_amount || meta.contract_period || (meta.infrastructure_notes || []).length > 0) && (
          <Section icon={<ShieldCheck size={16} className="text-[#4040E0]" />} title="Commercials & delivery">
            <ul className="space-y-1 text-sm text-[#475068]">
              {meta.emd_amount && (
                <li>
                  <span className="font-medium text-[#2c3345]">EMD:</span> {meta.emd_amount}
                </li>
              )}
              {meta.contract_period && (
                <li>
                  <span className="font-medium text-[#2c3345]">Contract period:</span> {meta.contract_period}
                </li>
              )}
              {(meta.infrastructure_notes || []).map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </Section>
        )}
      </div>

      {criteria.length > 0 && (
        <Section icon={<CheckCircle2 size={16} className="text-[#4040E0]" />} title="Key bidder criteria">
          <Bullets items={criteria} />
        </Section>
      )}
    </div>
  );
};
