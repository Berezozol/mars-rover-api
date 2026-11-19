import { EmptyState } from "../components/States";
import { useTitle } from "../lib/hooks";
import { Link } from "../router";

export function NotFound() {
  useTitle("Page not found — Sol Index");
  return (
    <EmptyState
      title="Page not found"
      actions={
        <Link class="btn btn-sm btn-primary" href="/">
          Go to the rovers
        </Link>
      }
    >
      There is nothing at this address.
    </EmptyState>
  );
}
