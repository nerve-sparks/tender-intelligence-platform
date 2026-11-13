import { useMemo, useState } from "react";
import { SendHorizontal } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import ShareTender from "@/components/modals/ShareTender";
import TenderMatchCard from "@/components/tenders/TenderMatchCardNew";
import { useToastSimple } from "@/components/ui/toaster-simple";
import { getCompanyId } from "@/lib/auth";
import { fetchCompanyMatches, updateTenderAction } from "@/services/tenderAgentApi";

const TendersPage = () => {
  const navigate = useNavigate();
  const companyId = getCompanyId();
  const { pushToast } = useToastSimple();
  const queryClient = useQueryClient();

  const [search, setSearch] = useState("");
  const [timePeriod, setTimePeriod] = useState("latest");
  const [sort, setSort] = useState("best_match");
  const [page, setPage] = useState(1);
  const [selectedShareTenderId, setSelectedShareTenderId] = useState<string | null>(null);

  const params = useMemo(
    () => ({
      q: search || undefined,
      time_period: timePeriod,
      sort,
      min_score: 0.7,
      page,
      limit: 5,
    }),
    [search, timePeriod, sort, page]
  );

  const { data, isLoading } = useQuery({
    queryKey: ["matches", companyId, params],
    queryFn: () => fetchCompanyMatches(companyId || "", params),
    enabled: Boolean(companyId),
  });

  const actionMutation = useMutation({
    mutationFn: ({ tenderId, action }: { tenderId: string; action: "saved" | "applied" | "discarded" | null }) =>
      updateTenderAction(tenderId, {
        company_id: companyId || "",
        action,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["matches", companyId] });
      queryClient.invalidateQueries({ queryKey: ["dashboardStats", companyId] });
    },
    onError: () => {
      pushToast("Unable to update tender action", "error");
    },
  });

  const totalPages = Math.max(1, Math.ceil((data?.total || 0) / (data?.limit || 1)));

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold text-[#232937] md:text-4xl">Current Active Tenders</h1>
          <p className="text-sm text-[#7d8599]">{data?.total || 0} results</p>
        </div>

        <div className="flex items-center gap-2">
          <div>
            <p className="text-xs text-[#6f768b]">Time Period</p>
            <select
              value={timePeriod}
              onChange={(event) => setTimePeriod(event.target.value)}
              className="h-10 rounded border border-[#d3d7e2] bg-white px-3 text-sm"
            >
              <option value="latest">Latest</option>
              <option value="7d">7 Days</option>
              <option value="30d">30 Days</option>
            </select>
          </div>
          <div>
            <p className="text-xs text-[#6f768b]">Sort By</p>
            <select
              value={sort}
              onChange={(event) => setSort(event.target.value)}
              className="h-10 rounded border border-[#d3d7e2] bg-white px-3 text-sm"
            >
              <option value="best_match">Best Match (&ge;70%)</option>
              <option value="latest">Latest</option>
            </select>
          </div>
        </div>
      </div>

      <div className="mb-4 flex items-center gap-2">
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search tender"
          className="h-11 flex-1 rounded border border-[#d5d9e3] bg-white px-4 text-sm"
        />
        <button className="flex h-10 w-10 items-center justify-center rounded-full bg-[#4040E0] text-white">
          <SendHorizontal size={16} />
        </button>
      </div>

      {isLoading ? <p className="text-sm text-[#6f768b]">Loading matches...</p> : null}

      <div className="space-y-3">
        {(data?.items || []).map((item) => (
          <TenderMatchCard
            key={item.tender.id}
            item={item}
            onOpen={(tenderId) => navigate(`/tenders/${tenderId}`)}
            onAction={(tenderId, action) => actionMutation.mutate({ tenderId, action })}
            onShare={(tenderId) => setSelectedShareTenderId(tenderId)}
          />
        ))}
      </div>

      <div className="mt-5 flex items-center justify-between">
        <button
          disabled={page <= 1}
          onClick={() => setPage((prev) => Math.max(1, prev - 1))}
          className="rounded-md bg-[#eff1f5] px-4 py-2 text-xs text-[#7f8798] disabled:opacity-60"
        >
          Prev
        </button>

        <div className="flex items-center gap-2">
          {Array.from({ length: totalPages }).map((_, index) => {
            const value = index + 1;
            return (
              <button
                key={value}
                onClick={() => setPage(value)}
                className={[
                  "h-8 w-8 rounded-full text-xs",
                  value === page ? "bg-[#4040E0] text-white" : "bg-[#eff1f5] text-[#7f8798]",
                ].join(" ")}
              >
                {value}
              </button>
            );
          })}
        </div>

        <button
          disabled={page >= totalPages}
          onClick={() => setPage((prev) => Math.min(totalPages, prev + 1))}
          className="rounded-md bg-[#eff1f5] px-4 py-2 text-xs text-[#7f8798] disabled:opacity-60"
        >
          Next
        </button>
      </div>

      <ShareTender
        open={Boolean(selectedShareTenderId)}
        onClose={() => setSelectedShareTenderId(null)}
        tenderId={selectedShareTenderId || ""}
        companyId={companyId || ""}
      />
    </div>
  );
};

export default TendersPage;
