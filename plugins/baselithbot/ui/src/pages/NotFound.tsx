import { Link } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";

export function NotFound() {
  return (
    <div>
      <PageHeader
        eyebrow="404"
        title="Route not found"
        description="The path you requested is not part of the dashboard."
      />
      <Link to="/" className="btn primary">
        Back to Overview
      </Link>
    </div>
  );
}
