import type { ReactNode } from "react";
import { Icon } from "../lib/icons";

type Accent = "teal" | "violet" | "cyan" | "amber" | "rose";

interface Props {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  iconPath?: string;
  accent?: Accent;
}

export function StatCard({
  label,
  value,
  sub,
  iconPath,
  accent = "teal",
}: Props) {
  return (
    <div className={`stat ${accent}`}>
      <div className="stat-label">
        {iconPath && (
          <span className="stat-icon">
            <Icon path={iconPath} size={14} />
          </span>
        )}
        {label}
      </div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}
