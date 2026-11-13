import { useMemo, useState } from "react";
import { ArrowLeft, Download, MapPin, Share2, Sparkles } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";

import DiscussWithAI from "@/components/modals/DiscussWithAI";
import ShareTender from "@/components/modals/ShareTender";
import { MatchVerdict, procurementLabel, TenderBriefSections } from "@/components/tenders/TenderBrief";
import { useToastSimple } from "@/components/ui/toaster-simple";
import { getCompanyId } from "@/lib/auth";
import { downloadTenderDocument, fetchTenderDetail, fetchTenderMatch, updateTenderAction } from "@/services/tenderAgentApi";

const TenderDetailsPage = () => {
  const { id = "" } = useParams();
  const companyId = getCompanyId() || "";
  const navigate = useNavigate();
  const { pushToast } = useToastSimple();
  const queryClient = useQueryClient();

  const [openDiscuss, setOpenDiscuss] = useState(false);
  const [openShare, setOpenShare] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const { data: tender } = useQuery({
    queryKey: ["tender", id],
    queryFn: () => fetchTenderDetail(id),
    enabled: Boolean(id),
  });

  const actionMutation = useMutation({
    mutationFn: (action: "saved" | "applied" | "discarded" | null) =>
      updateTenderAction(id, { company_id: companyId, action }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["matches", companyId] });
      queryClient.invalidateQueries({ queryKey: ["myList", companyId] });
      pushToast("Tender updated", "success");
    },
    onError: () => pushToast("Unable to update action", "error"),
  });

  const meta = tender?.metadata || {};
  const scraped = tender?.scraped_info || {};
  const title = meta.title || scraped.items || tender?.bid_id;

  const amount = useMemo(() => meta.estimated_value || scraped.bid_value_range || "N/A", [meta, scraped]);

  const { data: match } = useQuery({
    queryKey: ["tenderMatch", companyId, id],
    queryFn: () => fetchTenderMatch(companyId, id),
    enabled: Boolean(id && companyId),
    staleTime: 5 * 60 * 1000,
  });

  const daysLeft = useMemo(() => {
    if (!scraped.end_date) return null;
    const end = new Date(scraped.end_date).getTime();
    if (Number.isNaN(end)) return null;
    return Math.max(0, Math.ceil((end - Date.now()) / 86_400_000));
  }, [scraped.end_date]);

  return (
    <div>
      <button onClick={() => navigate("/tenders")} className="mb-4 text-sm text-[#4040E0]">
        <ArrowLeft size={14} className="mr-1 inline" /> Back to Search
      </button>

      <div className="rounded-xl border border-[#d8dce6] bg-white p-6">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap gap-2 text-[10px]">
              <span className="rounded-full bg-[#4040E0] px-2 py-1 text-white">GeM</span>
              <span className="rounded-full bg-[#eef0ff] px-2 py-1 text-[#4040E0]">Active</span>
              {procurementLabel(meta.procurement_type) && (
                <span className="rounded-full border border-[#dde1eb] px-2 py-1 text-[#6d7386]">
                  {procurementLabel(meta.procurement_type)}
                </span>
              )}
              {meta.sector && (
                <span className="rounded-full border border-[#dde1eb] px-2 py-1 text-[#6d7386]">{meta.sector}</span>
              )}
            </div>
            <h1 className="text-2xl font-semibold text-[#222835] md:text-4xl">{title}</h1>
          </div>
          <button
            onClick={() => setOpenDiscuss(true)}
            className="h-11 rounded-full bg-[#4040E0] px-5 text-sm text-white"
          >
            <Sparkles size={14} className="mr-1 inline" /> Discuss with AI
          </button>
        </div>

        <p className="text-xl font-semibold text-[#212734] md:text-2xl">
          {meta.buyer || meta.department || scraped.department || "Department"}
        </p>
        <p className="mt-2 text-sm text-[#5f667a]">
          <MapPin size={14} className="mr-1 inline" /> {meta.location || "India"}
        </p>

        <div className="mt-4 flex flex-wrap items-center gap-3 text-sm text-[#475068]">
          <span>Amount: {amount}</span>
          {meta.emd_amount && <span>EMD: {meta.emd_amount}</span>}
          <span>Closes: {String(scraped.end_date || "N/A").slice(0, 10)}</span>
          {daysLeft !== null && (
            <span
              className={`rounded px-2 py-1 text-xs ${
                daysLeft > 7
                  ? "bg-[#daf4eb] text-[#16835e]"
                  : daysLeft > 2
                    ? "bg-[#fdeecf] text-[#c98300]"
                    : "bg-[#fddcdb] text-[#d35353]"
              }`}
            >
              {daysLeft}d left
            </span>
          )}
        </div>

        <div className="mt-6 grid gap-4">
          {companyId && <MatchVerdict match={match} />}
          <TenderBriefSections tender={tender} />
        </div>

        <div className="mt-6 border-t border-[#ebedf4] pt-4">
          <div className="flex flex-wrap justify-end gap-2">
            <button
              onClick={() => actionMutation.mutate("discarded")}
              className="rounded-full border border-[#ef8f93] px-3 py-1.5 text-xs text-[#dd5056]"
            >
              Discard tender
            </button>
            <button
              onClick={() => setOpenShare(true)}
              className="rounded-full border border-[#d5d9e5] px-3 py-1.5 text-xs text-[#30374a]"
            >
              <Share2 size={12} className="mr-1 inline" /> Share
            </button>
            <button
              onClick={() => actionMutation.mutate("saved")}
              className="rounded-full border border-[#d5d9e5] px-3 py-1.5 text-xs text-[#30374a]"
            >
              Save
            </button>
            <button
              onClick={() => actionMutation.mutate("applied")}
              className="rounded-full border border-[#d5d9e5] px-3 py-1.5 text-xs text-[#30374a]"
            >
              Apply
            </button>
            <button
              onClick={async () => {
                setDownloading(true);
                try {
                  await downloadTenderDocument(id, tender?.bid_id);
                } catch {
                  pushToast("Could not download this bid document from GeM", "error");
                } finally {
                  setDownloading(false);
                }
              }}
              disabled={downloading}
              className="rounded-full bg-[#4040E0] px-3 py-1.5 text-xs text-white disabled:opacity-60"
            >
              <Download size={12} className="mr-1 inline" /> {downloading ? "Downloading..." : "Download Bid Document"}
            </button>
          </div>
        </div>
      </div>

      <DiscussWithAI
        open={openDiscuss}
        onClose={() => setOpenDiscuss(false)}
        tenderId={id}
        companyId={companyId}
      />

      <ShareTender
        open={openShare}
        onClose={() => setOpenShare(false)}
        tenderId={id}
        companyId={companyId}
      />
    </div>
  );
};

export default TenderDetailsPage;
