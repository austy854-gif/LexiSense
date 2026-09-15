"""
Celery tasks for Contract Agent execution.
Handles scheduled agent runs, monitoring, and alerting.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from celery import shared_task
from bson import ObjectId

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


def cron_to_next_run(cron_expr: str, from_time: datetime = None) -> Optional[datetime]:
    """Convert cron expression to next run datetime. Simplified implementation."""
    if not from_time:
        from_time = datetime.now(timezone.utc)
    
    # Simple parser for common cron patterns
    # Supports: "0 9 * * *" (daily 9 AM), "0 */6 * * *" (every 6 hours), etc.
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return from_time + timedelta(hours=24)
    
    minute, hour, day, month, dow = parts
    
    # For now, just handle daily at specific hour
    if minute.isdigit() and hour.isdigit() and day == "*" and month == "*" and dow == "*":
        target_hour = int(hour)
        target_minute = int(minute)
        next_run = from_time.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        if next_run <= from_time:
            next_run += timedelta(days=1)
        return next_run
    
    # Default: daily
    return from_time + timedelta(days=1)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def check_due_agents(self):
    """Check for agents that are due to run and queue their execution."""
    if not _db:
        logger.error("Database not initialized for agent task")
        return {"status": "error", "message": "Database not initialized"}

    logger.info("Checking for due contract agents...")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_check_due_agents_async())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Check due agents failed: {e}")
        raise self.retry(exc=e)


async def _check_due_agents_async():
    """Async implementation of due agent check."""
    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    now = datetime.now(timezone.utc)
    agents_queued = 0

    try:
        # Find active agents that are due
        cursor = db.contract_agents.find({
            "status": "active",
            "$or": [
                {"nextRunAt": {"$lte": now.isoformat()}},
                {"nextRunAt": {"$exists": False}},
                {"nextRunAt": None}
            ]
        })
        
        agents = await cursor.to_list(100)
        
        for agent in agents:
            # Queue the agent execution
            execute_contract_agent.delay(agent["id"])
            agents_queued += 1
            
            # Update next run time
            next_run = cron_to_next_run(agent.get("schedule", "0 9 * * *"), now)
            await db.contract_agents.update_one(
                {"id": agent["id"]},
                {"$set": {"nextRunAt": next_run.isoformat() if next_run else None}}
            )

        logger.info(f"Queued {agents_queued} contract agents for execution")
        return {"status": "success", "agents_queued": agents_queued}

    except Exception as e:
        logger.error(f"Check due agents failed: {e}")
        return {"status": "error", "message": str(e)}


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def execute_contract_agent(self, agent_id: str):
    """Execute a single contract agent."""
    if not _db:
        logger.error("Database not initialized for agent execution")
        return {"status": "error", "message": "Database not initialized"}

    logger.info(f"Executing contract agent: {agent_id}")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_execute_contract_agent_async(agent_id))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Agent execution failed for {agent_id}: {e}")
        raise self.retry(exc=e)


async def _execute_contract_agent_async(agent_id: str):
    """Async implementation of agent execution."""
    from services.ai_service import analyze_contract, get_chat_response
    from services.audit_service import log_action, send_notification
    from models.agentic import AgentStatus, AgentExecutionLog

    db = get_db()
    if not db:
        return {"status": "error", "message": "Database not available"}

    start_time = datetime.now(timezone.utc)
    
    try:
        agent = await db.contract_agents.find_one({"id": agent_id})
        if not agent:
            return {"status": "error", "message": "Agent not found"}

        if agent.get("status") != "active":
            return {"status": "skipped", "message": "Agent not active"}

        config = agent.get("config", {})
        org_id = agent["organizationId"]
        user_id = agent["createdBy"]

        # Build query based on agent config
        query = {"organizationId": org_id, "status": {"$ne": "expired"}}
        
        if config.get("contract_types"):
            query["contractType"] = {"$in": config["contract_types"]}
        if config.get("risk_levels"):
            query["riskLevel"] = {"$in": config["risk_levels"]}
        if config.get("counterparties"):
            query["counterparty"] = {"$in": config["counterparties"]}
        if config.get("tags"):
            query["tags"] = {"$in": config["tags"]}

        # Time window
        lookback_days = config.get("lookback_days", 30)
        cutoff = (start_time - timedelta(days=lookback_days)).isoformat()
        query["createdAt"] = {"$gte": cutoff}

        contracts = await db.contracts.find(query, {"_id": 0}).to_list(500)
        
        contracts_checked = len(contracts)
        alerts_generated = 0
        actions_taken = []
        errors = []

        agent_type = agent.get("agentType", "expiration_monitor")

        for contract in contracts:
            try:
                if agent_type == "expiration_monitor":
                    # Check expiration
                    await _check_expiration(contract, agent, config, db, actions_taken)
                    alerts_generated += 1
                    
                elif agent_type == "risk_alert":
                    # Check risk level changes
                    await _check_risk_change(contract, agent, config, db, actions_taken)
                    alerts_generated += 1
                    
                elif agent_type == "compliance_checker":
                    # Check compliance rules
                    await _check_compliance(contract, agent, config, db, actions_taken)
                    alerts_generated += 1
                    
                elif agent_type == "obligation_tracker":
                    # Check obligation deadlines
                    await _check_obligations(contract, agent, config, db, actions_taken)
                    alerts_generated += 1
                    
                elif agent_type == "renewal_negotiator":
                    # Check renewal opportunities
                    await _check_renewal(contract, agent, config, db, actions_taken)
                    alerts_generated += 1

            except Exception as e:
                errors.append(f"Contract {contract.get('id')}: {str(e)}")
                logger.error(f"Error processing contract {contract.get('id')}: {e}")

        # Update agent stats
        duration_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
        
        execution_log = AgentExecutionLog(
            executed_at=start_time.isoformat(),
            contracts_checked=contracts_checked,
            alerts_generated=alerts_generated,
            actions_taken=actions_taken,
            errors=errors,
            duration_ms=duration_ms,
            status=AgentStatus.COMPLETED if not errors else AgentStatus.ERROR
        )

        await db.contract_agents.update_one(
            {"id": agent_id},
            {
                "$set": {
                    "lastRunAt": start_time.isoformat(),
                    "totalExecutions": agent.get("totalExecutions", 0) + 1,
                    "totalAlertsGenerated": agent.get("totalAlertsGenerated", 0) + alerts_generated,
                    "updatedAt": datetime.now(timezone.utc).isoformat()
                },
                "$push": {"executionHistory": execution_log.model_dump()}
            }
        )

        # Audit log
        await log_action(
            organization_id=org_id,
            user_id=user_id,
            user_email="system",
            action="agent_executed",
            resource_type="contract_agent",
            resource_id=agent_id,
            resource_title=agent.get("name"),
            details={
                "contracts_checked": contracts_checked,
                "alerts_generated": alerts_generated,
                "actions_count": len(actions_taken),
                "errors_count": len(errors)
            }
        )

        logger.info(f"Agent {agent_id} completed: {contracts_checked} checked, {alerts_generated} alerts")
        return {
            "status": "success",
            "agent_id": agent_id,
            "contracts_checked": contracts_checked,
            "alerts_generated": alerts_generated,
            "actions_taken": len(actions_taken),
            "errors": len(errors)
        }

    except Exception as e:
        logger.error(f"Agent execution failed: {e}")
        # Log failed execution
        await db.contract_agents.update_one(
            {"id": agent_id},
            {
                "$push": {
                    "executionHistory": AgentExecutionLog(
                        executed_at=start_time.isoformat(),
                        contracts_checked=0,
                        alerts_generated=0,
                        actions_taken=[],
                        errors=[str(e)],
                        duration_ms=int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000),
                        status=AgentStatus.ERROR
                    ).model_dump()
                }
            }
        )
        raise


async def _check_expiration(contract, agent, config, db, actions_taken):
    """Check contract expiration."""
    expiry_date = contract.get("expiryDate")
    if not expiry_date:
        return
    
    try:
        expiry = datetime.fromisoformat(expiry_date.replace("Z", "+00:00"))
        days_until = (expiry - datetime.now(timezone.utc)).days
        
        threshold = config.get("alert_threshold", 30)
        if days_until <= threshold and days_until >= 0:
            # Create notification
            await _create_agent_alert(
                contract, agent, "expiration_warning",
                f"Contract expires in {days_until} days",
                db, actions_taken
            )
    except Exception as e:
        logger.error(f"Expiration check error: {e}")


async def _check_risk_change(contract, agent, config, db, actions_taken):
    """Check for risk level changes."""
    # Compare current risk with last assessment
    current_risk = contract.get("riskLevel")
    if not current_risk:
        return
    
    # Check if risk is high/critical
    if current_risk in ["high", "critical"]:
        await _create_agent_alert(
            contract, agent, "high_risk_detected",
            f"Contract has {current_risk} risk level",
            db, actions_taken
        )


async def _check_compliance(contract, agent, config, db, actions_taken):
    """Check compliance rules."""
    # Placeholder for compliance checking logic
    # Would integrate with playbook rules
    pass


async def _check_obligations(contract, agent, config, db, actions_taken):
    """Check obligation deadlines."""
    obligations = await db.extracted_obligations.find({
        "contractId": contract["id"],
        "status": {"$in": ["pending", "in_progress"]}
    }).to_list(100)
    
    for obl in obligations:
        due_date = obl.get("due_date")
        if due_date:
            try:
                due = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
                days_until = (due - datetime.now(timezone.utc)).days
                if days_until <= 7 and days_until >= 0:
                    await _create_agent_alert(
                        contract, agent, "obligation_due",
                        f"Obligation '{obl.get('title')}' due in {days_until} days",
                        db, actions_taken
                    )
            except Exception:
                pass


async def _check_renewal(contract, agent, config, db, actions_taken):
    """Check renewal opportunities."""
    expiry_date = contract.get("expiryDate")
    if not expiry_date:
        return
    
    try:
        expiry = datetime.fromisoformat(expiry_date.replace("Z", "+00:00"))
        days_until = (expiry - datetime.now(timezone.utc)).days
        
        # Renewal window: 90-60 days before expiry
        if 60 <= days_until <= 90:
            await _create_agent_alert(
                contract, agent, "renewal_opportunity",
                f"Contract renewal window open ({days_until} days until expiry)",
                db, actions_taken
            )
    except Exception as e:
        logger.error(f"Renewal check error: {e}")


async def _create_agent_alert(contract, agent, alert_type, message, db, actions_taken):
    """Create an in-app notification for agent alert."""
    from services.audit_service import send_notification
    
    # Get admin users
    admins = await db.users.find(
        {"organizationId": agent["organizationId"], "role": {"$in": ["admin", "manager"]}},
        {"_id": 0, "id": 1}
    ).to_list(50)
    
    for admin in admins:
        await send_notification(
            organization_id=agent["organizationId"],
            user_id=admin["id"],
            notification_type=f"agent_{alert_type}",
            title=f"Agent Alert: {agent.get('name')}",
            message=f"{message} - Contract: {contract.get('title')}",
            resource_type="contract",
            resource_id=contract["id"]
        )
    
    actions_taken.append({
        "type": "notification",
        "alert_type": alert_type,
        "contract_id": contract["id"],
        "recipients": len(admins)
    })