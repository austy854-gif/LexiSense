import { useCallback, useMemo, useState } from 'react';

import { clampPage, pageCount, pageOffset } from '../lib/apiHelpers.mjs';

/**
 * Client-side pagination state for a server-paginated list endpoint.
 *
 * The backend bounds `limit`/`offset` and returns the matching total in the
 * `X-Total-Count` header; this hook owns the page/pageSize state and derives
 * the `offset` to send. Call `setTotal(parseTotalCount(res.headers))` after
 * each fetch so the page count stays in sync.
 */
export function usePagination({ pageSize: initialPageSize = 25 } = {}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeState] = useState(initialPageSize);
  const [total, setTotal] = useState(0);

  const totalPages = useMemo(() => pageCount(total, pageSize), [total, pageSize]);
  const offset = useMemo(() => pageOffset(page, pageSize), [page, pageSize]);

  const goToPage = useCallback(
    (next) => setPage(clampPage(next, total, pageSize)),
    [total, pageSize]
  );

  const reset = useCallback(() => setPage(1), []);

  const setPageSize = useCallback((size) => {
    setPageSizeState(size);
    setPage(1);
  }, []);

  return { page, pageSize, total, totalPages, offset, setTotal, goToPage, setPageSize, reset };
}
