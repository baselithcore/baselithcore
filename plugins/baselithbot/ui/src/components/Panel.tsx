import type { ReactNode } from "react";

interface Props {
  title?: ReactNode;
  tag?: string;
  children: ReactNode;
  padded?: boolean;
  className?: string;
}

export function Panel({
  title,
  tag,
  children,
  padded = true,
  className,
}: Props) {
  return (
    <section
      className={["panel", padded ? "padded" : "", className ?? ""]
        .filter(Boolean)
        .join(" ")}
    >
      {(title || tag) && (
        <div className="panel-header">
          <h2>{title}</h2>
          {tag && <span className="tag">{tag}</span>}
        </div>
      )}
      {children}
    </section>
  );
}
