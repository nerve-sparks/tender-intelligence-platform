import { PropsWithChildren } from "react";
import { ChevronDown, LogOut } from "lucide-react";
import { NavLink, useNavigate } from "react-router-dom";

import BrandMark from "@/components/layout/BrandMark";
import NotificationBell from "@/components/layout/NotificationBell";
import { clearAuth } from "@/lib/auth";

const navItems = [
  { label: "Dashboard", to: "/dashboard" },
  { label: "Finding Tenders", to: "/tenders" },
  { label: "My List", to: "/my-list" },
  { label: "My Organization", to: "/organization" },
  { label: "Help", to: "/help" },
];

const AppShell = ({ children }: PropsWithChildren) => {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-[#eceef3]">
      <header className="border-b border-[#d9dce5] bg-white">
        <div className="mx-auto flex h-16 max-w-[1280px] items-center justify-between px-8">
          <BrandMark />

          <nav className="flex max-w-[55%] items-center gap-2 overflow-x-auto text-xs text-[#4f5565] md:gap-5 md:text-[13px]">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  [
                    "rounded-md px-2 py-1 transition",
                    isActive ? "bg-[#eef0ff] text-[#4040E0]" : "hover:text-[#4040E0]",
                  ].join(" ")
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="flex items-center gap-4">
            <NotificationBell />
            <button
              className="flex items-center gap-2 rounded-full border border-[#d6d9e3] bg-white px-2 py-1 text-xs text-[#5f6577]"
              onClick={() => {
                clearAuth();
                navigate("/auth");
              }}
            >
              <div className="h-7 w-7 rounded-full bg-[#d8dce8]" />
              <span className="hidden sm:inline">Account</span>
              <ChevronDown size={12} />
              <LogOut size={12} />
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1280px] px-8 py-7">{children}</main>
    </div>
  );
};

export default AppShell;
