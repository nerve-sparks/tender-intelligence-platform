import { useMemo, useState } from "react";
import { format } from "date-fns";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { getCompanyId } from "@/lib/auth";
import { fetchDashboardReport, fetchDashboardStats, triggerScrapeAnalyze } from "@/services/tenderAgentApi";
import { useToastSimple } from "@/components/ui/toaster-simple";

const DashboardPage = () => {
  const companyId = getCompanyId() || undefined;
  const navigate = useNavigate();
  const { pushToast } = useToastSimple();
  const [range, setRange] = useState("12m");

  const { data: stats } = useQuery({
    queryKey: ["dashboardStats", companyId],
    queryFn: () => fetchDashboardStats(companyId),
    enabled: Boolean(companyId),
  });

  const { data: report } = useQuery({
    queryKey: ["dashboardReport", companyId, range],
    queryFn: () => fetchDashboardReport(range, companyId),
    enabled: Boolean(companyId),
  });

  const chartData = useMemo(() => {
    if (!report) return [];
    return report.labels.map((label, index) => ({
      label,
      gathering: report.series.gathering[index] || 0,
      analyze: report.series.analyze[index] || 0,
      saved: report.series.saved[index] || 0,
    }));
  }, [report]);

  const todayText = `Today is ${format(new Date(), "EEEE, d MMMM yyyy")}`;

  const cards = [
    {
      title: "TOTAL TENDERS ANALYZED",
      value: stats?.totals.tenders_analyzed || 0,
    },
    {
      title: "BEST TENDERS FOUND",
      value: stats?.totals.best_tenders_found || 0,
    },
    {
      title: "TENDERS SAVED",
      value: stats?.totals.tenders_saved || 0,
    },
    {
      title: "TENDERS APPLIED",
      value: stats?.totals.tenders_applied || 0,
    },
  ];

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold text-[#2a3040] md:text-4xl">Hello, [{stats?.company_name || "Company Name"}]</h1>
          <p className="mt-1 text-base text-[#4d556c] md:text-xl">{todayText}</p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={async () => {
              try {
                const result = await triggerScrapeAnalyze();
                if (result?.already_running) {
                  pushToast("A scrape is already running - new tenders appear as they are analysed", "success");
                } else {
                  pushToast("Scrape & Analyze started - this takes a while, new tenders appear as they are analysed", "success");
                }
              } catch {
                pushToast("Unable to trigger scrape job", "error");
              }
            }}
            className="h-11 rounded-full border border-[#4040E0] px-5 text-sm text-[#4040E0]"
          >
            Scrape & Analyze now
          </button>
          <button
            onClick={() => navigate("/tenders")}
            className="h-11 rounded-full bg-[#4040E0] px-7 text-base text-white"
          >
            Find Matching Tenders
          </button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <div key={card.title} className="rounded-xl border border-[#d8dce6] bg-white p-4">
            <p className="text-xs tracking-[0.2em] text-[#8a92a7]">{card.title}</p>
            <p className="mt-1 text-3xl font-semibold text-[#1f2533]">{card.value}</p>
          </div>
        ))}
      </div>

      <div className="mt-5 grid gap-4 xl:grid-cols-[1fr_320px]">
        <div className="rounded-xl border border-[#d8dce6] bg-white p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-xl font-semibold text-[#262b38]">Tenders Report</h2>
            <select
              value={range}
              onChange={(event) => setRange(event.target.value)}
              className="rounded border border-[#d3d7e2] bg-white px-2 py-1 text-xs"
            >
              <option value="7d">7 Days</option>
              <option value="10d">10 Days</option>
              <option value="6m">6 Months</option>
              <option value="12m">12 Months</option>
            </select>
          </div>

          <div className="h-[320px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid stroke="#eef0f5" vertical={false} />
                <XAxis dataKey="label" fontSize={11} />
                <YAxis fontSize={11} />
                <Tooltip />
                <Bar dataKey="gathering" fill="#b4b8ff" radius={[4, 4, 0, 0]} />
                <Bar dataKey="analyze" fill="#4040E0" radius={[4, 4, 0, 0]} />
                <Bar dataKey="saved" fill="#2028a8" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="rounded-xl border border-[#d8dce6] bg-white p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-xl font-semibold text-[#262b38]">Tenders Overview</h2>
            <p className="text-xs text-[#7f8799]">Last 7 Days</p>
          </div>

          <div className="space-y-4 text-sm text-[#434a5f]">
            <div className="flex justify-between">
              <span>Tenders Gathering</span>
              <strong>{stats?.overview_last_7_days.gathering || 0}</strong>
            </div>
            <div className="flex justify-between">
              <span>Tenders Analyzed</span>
              <strong>{stats?.overview_last_7_days.analyzed || 0}</strong>
            </div>
            <div className="flex justify-between">
              <span>Tenders Saved</span>
              <strong>{stats?.overview_last_7_days.saved || 0}</strong>
            </div>
            <div className="flex justify-between">
              <span>Tenders Applied</span>
              <strong>{stats?.overview_last_7_days.applied || 0}</strong>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DashboardPage;
