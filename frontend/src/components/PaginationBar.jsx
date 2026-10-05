import { ChevronLeft, ChevronRight } from 'lucide-react';

import { Button } from './ui/button';
import { MAX_PAGE_SIZE } from '../lib/apiHelpers.mjs';

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100].filter((n) => n <= MAX_PAGE_SIZE);

/**
 * Accessible pagination controls for a server-paginated list.
 *
 * Renders nothing when there are no records. The range summary and the
 * page-size selector are labelled for screen readers, and the prev/next
 * buttons carry explicit `aria-label`s.
 */
export function PaginationBar({
  page,
  totalPages,
  total,
  pageSize,
  onPageChange,
  onPageSizeChange,
  loading = false,
  itemLabel = 'items',
}) {
  if (!total) return null;

  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);

  return (
    <div
      className="flex flex-col sm:flex-row items-center justify-between gap-3 px-4 py-3 border-t border-border"
      data-testid="pagination-bar"
    >
      <p className="text-sm text-muted-foreground" aria-live="polite">
        Showing <span className="font-medium text-foreground">{start}</span>–
        <span className="font-medium text-foreground">{end}</span> of{' '}
        <span className="font-medium text-foreground">{total}</span> {itemLabel}
      </p>

      <div className="flex items-center gap-2">
        <label htmlFor="page-size" className="text-sm text-muted-foreground">
          Rows per page
        </label>
        <select
          id="page-size"
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
          disabled={loading}
          className="h-8 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          data-testid="page-size"
        >
          {PAGE_SIZE_OPTIONS.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>

        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page - 1)}
          disabled={loading || page <= 1}
          aria-label="Go to previous page"
          data-testid="page-prev"
        >
          <ChevronLeft className="h-4 w-4" />
          Prev
        </Button>

        <span className="text-sm text-muted-foreground whitespace-nowrap">
          Page {page} of {totalPages}
        </span>

        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page + 1)}
          disabled={loading || page >= totalPages}
          aria-label="Go to next page"
          data-testid="page-next"
        >
          Next
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
