"""
Celery tasks for scheduled jobs.
These tasks run in separate worker processes via Celery Beat.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from celery import shared_task

logger = logging.getLogger(__name__)

# Database reference (set by init_scheduler)
_db = None


def set_database(database):
    """Set the database reference for tasks."""
    global _db
    _db = database


def get_db():
    """Get the database reference."""
    return _db


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def check_and_send_daily_alerts(self):
    """Check for expiring contracts and send alerts. Runs daily at 9 AM UTC."""
    if not _db:
        logger.error("Database not initialized for scheduler task")
        return {"status": "error", "message": "Database not initialized"}

    logger.info("Running scheduled alert check...")

    try:
        # Run async function in event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_check_and_send_daily_alerts_async())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Scheduled alert check failed: {e}")
        raise self.retry(exc=e)


async def _check_and_send_daily_alerts_async():
    """Async implementation of daily alert check."""
    from services.email_service import send_expiration_alert
    from models.alerts import ExpirationAlert

    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    total_alerts_sent = 0

    try:
        # Get all organizations
        organizations = await db.organizations.find({}, {"_id": 0, "id": 1}).to_list(1000)

        for org in organizations:
            org_id = org["id"]

            # Get alert settings for this organization
            settings = await db.alert_settings.find_one(
                {"organizationId": org_id},
                {"_id": 0}
            )

            if not settings:
                settings = {"alertDays": [30, 14, 7, 1], "emailEnabled": True}

            if not settings.get("emailEnabled", True):
                continue

            today = datetime.now(timezone.utc)

            # Get all admin users in the organization
            admins = await db.users.find(
                {"organizationId": org_id, "role": {"$in": ["admin", "manager"]}},
                {"_id": 0, "id": 1, "email": 1}
            ).to_list(100)

            if not admins:
                continue

            for alert_day in settings.get("alertDays", [30, 14, 7, 1]):
                target_date = (today + timedelta(days=alert_day)).strftime("%Y-%m-%d")

                # Find contracts expiring on this specific day
                expiring_contracts = await db.contracts.find(
                    {
                        "organizationId": org_id,
                        "expiryDate": target_date,
                        "status": {"$ne": "expired"}
                    },
                    {"_id": 0}
                ).to_list(100)

                for contract in expiring_contracts:
                    # Check if alert was already sent
                    existing_alert = await db.expiration_alerts.find_one({
                        "contractId": contract["id"],
                        "daysBeforeExpiry": alert_day,
                        "emailSent": True
                    })

                    if existing_alert:
                        continue

                    # Send alert to all admins/managers
                    for admin in admins:
                        await send_expiration_alert(
                            to_email=admin["email"],
                            contract_id=contract["id"],
                            contract_title=contract.get("title", "Untitled"),
                            counterparty=contract.get("counterparty", "Not specified"),
                            expiry_date=contract.get("expiryDate", "Unknown"),
                            days_remaining=alert_day
                        )
                        total_alerts_sent += 1

                    # Record alert as sent
                    alert = ExpirationAlert(
                        contractId=contract["id"],
                        userId="system",
                        daysBeforeExpiry=alert_day,
                        emailSent=True,
                        emailSentAt=datetime.now(timezone.utc).isoformat()
                    )
                    await db.expiration_alerts.insert_one(alert.model_dump())

        logger.info(f"Scheduled alert check completed. {total_alerts_sent} alerts sent.")
        return {"status": "success", "alerts_sent": total_alerts_sent}

    except Exception as e:
        logger.error(f"Scheduled alert check failed: {e}")
        return {"status": "error", "message": str(e)}


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def cleanup_old_audit_logs(self):
    """Clean up audit logs older than retention period. Runs weekly."""
    if not _db:
        logger.error("Database not initialized for cleanup task")
        return {"status": "error", "message": "Database not initialized"}

    logger.info("Running audit log cleanup...")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_cleanup_old_audit_logs_async())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Audit log cleanup failed: {e}")
        raise self.retry(exc=e)


async def _cleanup_old_audit_logs_async():
    """Async implementation of audit log cleanup."""
    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        # Keep audit logs for 90 days
        retention_days = 90
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

        result = await db.audit_logs.delete_many({
            "createdAt": {"$lt": cutoff_date}
        })

        logger.info(f"Audit log cleanup completed. Deleted {result.deleted_count} old logs.")
        return {"status": "success", "deleted_count": result.deleted_count}

    except Exception as e:
        logger.error(f"Audit log cleanup failed: {e}")
        return {"status": "error", "message": str(e)}


@shared_task(bind=True, max_retries=1)
def optimize_database_indexes(self):
    """Optimize database indexes. Runs daily."""
    if not _db:
        logger.error("Database not initialized for index optimization")
        return {"status": "error", "message": "Database not initialized"}

    logger.info("Running database index optimization...")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_optimize_database_indexes_async())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Database index optimization failed: {e}")
        return {"status": "error", "message": str(e)}


async def _optimize_database_indexes_async():
    """Async implementation of index optimization."""
    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    try:
        # Rebuild indexes for collections that may benefit
        collections = ["contracts", "audit_logs", "notifications", "expiration_alerts"]
        rebuilt = 0

        for coll_name in collections:
            try:
                await db[coll_name].reIndex()
                rebuilt += 1
            except Exception as e:
                logger.warning(f"Could not reindex {coll_name}: {e}")

        logger.info(f"Database index optimization completed. Rebuilt {rebuilt} collections.")
        return {"status": "success", "collections_rebuilt": rebuilt}

    except Exception as e:
        logger.error(f"Database index optimization failed: {e}")
        return {"status": "error", "message": str(e)}