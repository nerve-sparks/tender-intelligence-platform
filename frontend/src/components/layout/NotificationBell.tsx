import { useEffect, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { getCompanyId } from "@/lib/auth";
import { fetchNotifications, markNotificationsRead } from "@/services/tenderAgentApi";

/** New-match alerts: GeM tenders that newly scored at or above the match threshold. */
const NotificationBell = () => {
  const companyId = getCompanyId() || "";
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const { data } = useQuery({
    queryKey: ["notifications", companyId],
    queryFn: () => fetchNotifications(companyId),
    enabled: Boolean(companyId),
    refetchInterval: 60_000,
  });

  const markRead = useMutation({
    mutationFn: () => markNotificationsRead(companyId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications", companyId] }),
  });

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const unread = data?.unread || 0;
  const items = data?.items || [];

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && unread > 0) markRead.mutate();
  };

  return (
    <div className="relative" ref={panelRef}>
      <button onClick={toggle} className="relative p-1" aria-label="New tender matches">
        <Bell size={16} className="text-[#6a7186]" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 min-w-[16px] rounded-full bg-[#dd5056] px-1 text-center text-[10px] leading-4 text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-[340px] max-w-[90vw] rounded-xl border border-[#d8dce6] bg-white p-3 shadow-lg">
          <p className="mb-2 text-sm font-semibold text-[#222835]">New matching tenders</p>
          {items.length === 0 ? (
            <p className="text-xs text-[#7a8195]">No new matches yet. They appear here after a scrape finds one.</p>
          ) : (
            <ul className="max-h-80 space-y-1 overflow-y-auto">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    onClick={() => {
                      setOpen(false);
                      navigate(`/tenders/${item.tender_id}`);
                    }}
                    className={`w-full rounded-md p-2 text-left hover:bg-[#f3f4f8] ${item.read ? "" : "bg-[#eef0ff]"}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="line-clamp-1 text-xs font-medium text-[#2c3345]">{item.title || item.bid_id}</span>
                      <span className="shrink-0 text-xs font-semibold text-[#4040E0]">{Math.round(item.score * 100)}%</span>
                    </div>
                    <p className="mt-0.5 text-[11px] text-[#7a8195]">
                      {item.bid_id}
                      {item.end_date ? ` · closes ${String(item.end_date).slice(0, 10)}` : ""}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
};

export default NotificationBell;
