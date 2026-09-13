"""
Structured logging configuration for LexiSense.
Provides JSON-formatted logs with correlation IDs for tracing.
"""
import logging
import sys
import json
import uuid
from typing import Any, Dict
from pythonjsonlogger import jsonlogger
from contextvars import ContextVar

# Context variable for request correlation ID
request_id_var: ContextVar[str] = ContextVar('request_id', default='')


class StructuredFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter that adds standard fields."""
    
    def add_fields(self, log_record: Dict[str, Any], record: logging.LogRecord, message_dict: Dict[str, Any]) -> None:
        super().add_fields(log_record, record, message_dict)
        
        # Standard fields
        log_record['timestamp'] = self.formatTime(record, self.datefmt)
        log_record['level'] = record.levelname
        log_record['logger'] = record.name
        log_record['module'] = record.module
        log_record['function'] = record.funcName
        log_record['line'] = record.lineno
        
        # Add correlation ID if available
        request_id = request_id_var.get()
        if request_id:
            log_record['request_id'] = request_id
        
        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 
                          'filename', 'module', 'lineno', 'funcName', 'created', 
                          'msecs', 'relativeCreated', 'thread', 'threadName', 
                          'processName', 'process', 'exc_info', 'exc_text', 'stack_info']:
                log_record[key] = value


def setup_logging(
    level: str = "INFO",
    json_format: bool = True,
    include_request_id: bool = True
) -> logging.Logger:
    """
    Configure application logging.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        json_format: Use JSON formatting (production) vs human-readable (dev)
        include_request_id: Include request correlation IDs
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    handler = logging.StreamHandler(sys.stdout)
    
    if json_format:
        formatter = StructuredFormatter(
            fmt='%(timestamp)s %(level)s %(logger)s %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S.%fZ'
        )
    else:
        formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
    
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    
    # Reduce noise from third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("motor").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    
    logger = logging.getLogger("lexisense")
    logger.info("Logging configured", extra={"json_format": json_format, "level": level})
    
    return logger


def get_request_id() -> str:
    """Get or generate a request correlation ID."""
    request_id = request_id_var.get()
    if not request_id:
        request_id = str(uuid.uuid4())[:8]
        request_id_var.set(request_id)
    return request_id


def set_request_id(request_id: str) -> None:
    """Set the request correlation ID for the current context."""
    request_id_var.set(request_id)


def clear_request_id() -> None:
    """Clear the request correlation ID."""
    request_id_var.set('')


class RequestLoggingMiddleware:
    """ASGI middleware for request/response logging with correlation IDs."""
    
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # Generate correlation ID
        request_id = get_request_id()
        
        # Extract request info
        method = scope.get("method", "")
        path = scope.get("path", "")
        query_string = scope.get("query_string", b"").decode()
        client = scope.get("client", ("unknown", 0))[0]
        
        logger = logging.getLogger("lexisense.request")
        
        # Log request start
        logger.info(
            "Request started",
            extra={
                "request_id": request_id,
                "method": method,
                "path": path,
                "query": query_string,
                "client_ip": client,
            }
        )
        
        # Capture response status
        status_code = 0
        
        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)
        
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            logger.exception(
                "Request failed",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "error": str(e),
                }
            )
            raise
        finally:
            # Log request completion
            logger.info(
                "Request completed",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                }
            )
            clear_request_id()


# Convenience function for getting logger with request context
def get_logger(name: str) -> logging.Logger:
    """Get a logger that automatically includes request context."""
    return logging.getLogger(f"lexisense.{name}")