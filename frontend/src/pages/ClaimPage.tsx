import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router";
import { ClaimBody } from "../components/ClaimView";
import { ErrorBox, Panel, Spinner } from "../components/ui";
import { api } from "../lib/api";

/** A shareable audit page for one claim (GET /api/claims/:id). */
export default function ClaimPage() {
  const { id } = useParams();
  const claim = useQuery({ queryKey: ["claim", id], queryFn: () => api.claim(id!), enabled: !!id });
  if (claim.isPending) return <Spinner label="Loading claim" />;
  if (claim.isError) return <ErrorBox error={claim.error} onRetry={() => claim.refetch()} />;
  return (
    <>
      <Link to={`/runs/${claim.data.run_id}`} className="mb-4 inline-flex items-center gap-1 text-[13px] font-medium text-accent hover:underline">
        <ArrowLeft className="size-4" /> Back to the run
      </Link>
      <Panel className="max-w-3xl"><ClaimBody claim={claim.data} /></Panel>
    </>
  );
}
