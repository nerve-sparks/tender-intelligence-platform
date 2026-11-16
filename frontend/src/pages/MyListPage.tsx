import { useMemo, useState } from "react";
import { Mail } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";

import ShareTender from "@/components/modals/ShareTender";
import TenderMatchCard from "@/components/tenders/TenderMatchCardNew";
import { getCompanyId } from "@/lib/auth";
import { fetchMyList, shareSavedTenders, updateTenderAction } from "@/services/tenderAgentApi";

const MyListPage = () => {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "saved";
  const companyId = getCompanyId() || "";
  const navigate = useNavigate();

  const [shareTenderId, setShareTenderId] = useState<string | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);

  const { data, refetch } = useQuery({
    queryKey: ["myList", companyId, tab],
    queryFn: () => fetchMyList(companyId, tab, 1, 20),
    enabled: Boolean(companyId),
  });

  const items = useMemo(() => {
    return (data?.items || [])
      .filter((entry: any) => entry.tender)
      .map((entry: any) => ({
        tender: entry.tender,
        match: entry.match || { score: 0, verified: false, reasons: [] },
        action: entry.action,
      }));
  }, [data]);

  const savedCount: number = tab === "saved" ? data?.total ?? items.length : 0;

  const sendAllSaved = async (recipients: string[]) => {
    const result = await shareSavedTenders(companyId, { recipients });
    if (!result.sent) throw new Error("Unable to send email");
    const tenders = `${result.tenders} tender${result.tenders === 1 ? "" : "s"}`;
    const people = `${result.count} recipient${result.count === 1 ? "" : "s"}`;
    const links = result.link_only ? ` (${result.link_only} as links only - size limit)` : "";
    return `Sent ${tenders} to ${people}${links}`;
  };

  return (
    <div>
      <h1 className="text-3xl font-semibold text-[#232937] md:text-4xl">My List</h1>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-2">
          {[
            { label: "Saved", value: "saved" },
            { label: "Applied", value: "applied" },
            { label: "Discarded", value: "discarded" },
          ].map((item) => (
            <button
              key={item.value}
              onClick={() => setParams({ tab: item.value })}
              className={[
                "rounded-full px-4 py-2 text-sm",
                tab === item.value ? "bg-[#4040E0] text-white" : "bg-white text-[#596179]",
              ].join(" ")}
            >
              {item.label}
            </button>
          ))}
        </div>

        {tab === "saved" && (
          <button
            onClick={() => setBulkOpen(true)}
            disabled={savedCount === 0}
            className="rounded-full bg-[#4040E0] px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            <Mail size={14} className="mr-1.5 inline" />
            Email all saved ({savedCount})
          </button>
        )}
      </div>

      <div className="mt-4 space-y-3">
        {items.map((item: any) => (
          <TenderMatchCard
            key={item.tender.id}
            item={item}
            onOpen={(tenderId) => navigate(`/tenders/${tenderId}`)}
            onAction={async (tenderId, action) => {
              await updateTenderAction(tenderId, { company_id: companyId, action });
              refetch();
            }}
            onShare={(tenderId) => setShareTenderId(tenderId)}
          />
        ))}
      </div>

      <ShareTender
        open={Boolean(shareTenderId)}
        onClose={() => setShareTenderId(null)}
        tenderId={shareTenderId || ""}
        companyId={companyId}
      />

      <ShareTender
        open={bulkOpen}
        onClose={() => setBulkOpen(false)}
        companyId={companyId}
        title={`Email all saved tenders (${savedCount})`}
        sendingLabel={`Sending ${savedCount} tenders with PDFs...`}
        onSend={sendAllSaved}
      />
    </div>
  );
};

export default MyListPage;
