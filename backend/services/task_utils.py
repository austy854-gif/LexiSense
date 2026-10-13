"""Helpers shared by the Celery task modules: tenant scoping and pagination."""
from typing import Any, AsyncIterator, Dict, List, Optional

#: Page size for org-wide scans (keyset pagination on the application ``id``).
BATCH_SIZE = 200


async def iter_batches(
    collection,
    query: Dict[str, Any],
    projection: Optional[Dict[str, Any]] = None,
    batch_size: int = BATCH_SIZE,
) -> AsyncIterator[List[Dict[str, Any]]]:
    """Yield every matching document in ``id`` order, ``batch_size`` at a time.

    Keyset pagination (``id > last_id``) instead of a single capped
    ``to_list(N)``, so org-wide jobs no longer silently stop at N documents.
    ``id`` is always included in the projection because it is the cursor.
    """
    proj = None
    if projection is not None:
        proj = dict(projection)
        proj["id"] = 1
    last_id = None
    while True:
        page_query = dict(query)
        if last_id is not None:
            page_query["id"] = {"$gt": last_id}
        batch = await collection.find(page_query, proj).sort("id", 1).limit(batch_size).to_list(batch_size)
        if not batch:
            return
        yield batch
        if len(batch) < batch_size:
            return
        last_id = batch[-1]["id"]


async def organization_ids(db) -> List[str]:
    """All organisation ids that own contracts (beat jobs loop per org)."""
    ids = await db.contracts.distinct("organizationId")
    return sorted(i for i in ids if i)
