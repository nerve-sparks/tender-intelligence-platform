import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";

import TenderMatchCard from "@/components/tenders/TenderMatchCardNew";
import { getCompanyId } from "@/lib/auth";
import { fetchMyList, updateTenderAction } from "@/services/tenderAgentApi";

const MyListPage = () => {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "saved";
  const companyId = getCompanyId() || "";
  const navigate = useNavigate();

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

  return (
    <div>
      <h1 className="text-3xl font-semibold text-[#232937] md:text-4xl">My List</h1>

      <div className="mt-4 flex gap-2">
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
            onShare={(tenderId) => navigate(`/tenders/${tenderId}`)}
          />
        ))}
      </div>
    </div>
  );
};

export default MyListPage;
