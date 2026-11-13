import { CalendarDays, Download, MapPin, Share2 } from "lucide-react";

import { useState } from "react";

import { downloadTenderDocument } from "@/services/tenderAgentApi";
import { MatchItem } from "@/types/tender-agent";

import { MatchLevels, procurementLabel, RecommendationBadge } from "./TenderBrief";

interface TenderMatchCardProps {
  item: MatchItem;
  onOpen: (tenderId: string) => void;
  onAction: (tenderId: string, action: "saved" | "applied" | "discarded" | null) => void;
  onShare: (tenderId: string) => void;
}

const badgeClass = "rounded-full border border-[#d6dae8] px-2 py-0.5 text-[11px] text-[#646d84]";

const TenderMatchCard = ({ item, onOpen, onAction, onShare }: TenderMatchCardProps) => {
  const [downloading, setDownloading] = useState(false);
  const tender = item.tender;
  const meta = tender.metadata || {};
  const scraped = tender.scraped_info || {};
  const title = meta.title || scraped.items || tender.bid_id;
  const department = meta.buyer || meta.department || scraped.department || "Department";
  const location = meta.location || "India";
  const match = item.match;
  const scorePercent = Math.round((match?.score || 0) * 100);
  const procurement = procurementLabel(match?.procurement_type || meta.procurement_type);

  return (
    <div className="rounded-xl border border-[#d8dce6] bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <button
            onClick={() => onOpen(tender.id)}
            className="truncate text-left text-lg font-semibold text-[#1f2533] hover:text-[#4040E0] md:text-xl"
          >
            {department}
          </button>
          <div className="mt-2 flex items-center gap-2 text-[13px] text-[#666e83]">
            <MapPin size={13} />
            <span>{location}</span>
            <span>•</span>
            <CalendarDays size={13} />
            <span>Published {String(scraped.start_date || "N/A").slice(0, 10)}</span>
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <span className="rounded-full bg-[#4040E0] px-2 py-1 text-[10px] text-white">GeM</span>
            {procurement && <span className={badgeClass}>{procurement}</span>}
            {meta.emd_amount && <span className={badgeClass}>EMD {meta.emd_amount}</span>}
            <RecommendationBadge match={match} />
          </div>
          <p className="mt-2 text-sm font-medium text-[#2c3345]">{title}</p>
          <p className="mt-1 line-clamp-2 text-sm text-[#5f667a]">{meta.what_is_being_bought || meta.summary}</p>
          <div className="mt-2">
            <MatchLevels match={match} />
          </div>
          {(match?.why_matches?.length || match?.gaps?.length) ? (
            <div className="mt-2 grid gap-1 text-xs text-[#5f667a] md:grid-cols-2">
              {match?.why_matches?.[0] && <p className="text-[#16835e]">✓ {match.why_matches[0]}</p>}
              {match?.gaps?.[0] && <p className="text-[#d35353]">! {match.gaps[0]}</p>}
            </div>
          ) : null}
        </div>

        <div
          className={`flex h-[86px] w-[86px] shrink-0 flex-col items-center justify-center rounded-full border-4 text-center ${
            match?.verified ? "border-[#4040E0]" : "border-dashed border-[#b8bdd0]"
          }`}
          title={match?.verified ? "Business relevance verified" : "Preliminary score - relevance check pending"}
        >
          <p className="text-lg font-semibold text-[#202532]">{scorePercent}%</p>
          <p className="text-[11px] text-[#687088]">{match?.verified ? "Matched" : "Preliminary"}</p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-[#ebedf4] pt-3">
        <div className="flex items-center gap-4 text-sm text-[#525a70]">
          <span className="font-medium text-[#159d76]">{scraped.bid_value_range || meta.estimated_value || "N/A"}</span>
          <span className="text-[#d75252]">{String(scraped.end_date || "N/A").slice(0, 10)}</span>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => onAction(tender.id, "discarded")}
            className="rounded-full border border-[#ef8f93] px-3 py-1.5 text-xs text-[#dd5056]"
          >
            Discard tender
          </button>
          <button
            onClick={() => onShare(tender.id)}
            className="rounded-full border border-[#d5d9e5] px-3 py-1.5 text-xs text-[#30374a]"
          >
            <Share2 size={12} className="mr-1 inline" /> Share
          </button>
          <button
            onClick={() => onAction(tender.id, item.action === "saved" ? null : "saved")}
            className="rounded-full border border-[#d5d9e5] px-3 py-1.5 text-xs text-[#30374a]"
          >
            {item.action === "saved" ? "Saved" : "Save"}
          </button>
          <button
            onClick={async () => {
              setDownloading(true);
              try {
                await downloadTenderDocument(tender.id, tender.bid_id);
              } catch {
                window.alert("Could not download this bid document from GeM right now. Please try again.");
              } finally {
                setDownloading(false);
              }
            }}
            disabled={downloading}
            className="rounded-full bg-[#4040E0] px-3 py-1.5 text-xs text-white disabled:opacity-60"
          >
            <Download size={12} className="mr-1 inline" /> {downloading ? "Downloading..." : "Download"}
          </button>
        </div>
      </div>
    </div>
  );
};

export default TenderMatchCard;
