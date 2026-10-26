import uuid
import asyncio
import logging
import re
from typing import Optional
import boto3
from botocore.exceptions import ClientError

from utils.env import get_env

logger = logging.getLogger(__name__)

AWS_ACCESS_KEY_ID = get_env("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = get_env("AWS_SECRET_ACCESS_KEY")
AWS_REGION = get_env("AWS_REGION", "us-east-1")


def get_bucket_name() -> Optional[str]:
    """The S3 bucket: ``AWS_S3_BUCKET``, else the legacy ``S3_BUCKET_NAME``.

    Single source of truth for storage *and* the /health probe. Read at call
    time so both always agree.
    """
    return get_env("AWS_S3_BUCKET") or get_env("S3_BUCKET_NAME") or None


#: Back-compat alias (value at import time). Prefer :func:`get_bucket_name`.
S3_BUCKET_NAME = get_bucket_name()


def get_s3_client():
    """Get an S3 client instance."""
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY or not get_bucket_name():
        logger.warning("AWS credentials not fully configured - using mock storage (NOT FOR PRODUCTION)")
        return None

    return boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION
    )


def _safe_extension(filename: str, extension: Optional[str]) -> str:
    raw = extension or ""
    if not raw and filename and "." in filename:
        raw = filename.rsplit(".", 1)[-1]
    cleaned = re.sub(r"[^a-z0-9]", "", (raw or "bin").lower())[:8]
    return cleaned or "bin"


async def upload_file_to_s3(
    file_content: bytes,
    filename: str,
    organization_id: str,
    content_type: str = "application/octet-stream",
    extension: Optional[str] = None,
) -> str:
    """Upload a file to S3 and return the storage key.

    ``extension`` should be the sniffed extension (``pdf`` or ``txt``). The
    original filename is never interpolated into the key.
    """
    file_ext = _safe_extension(filename, extension)
    storage_key = f"{organization_id}/{uuid.uuid4()}.{file_ext}"

    s3_client = get_s3_client()
    if not s3_client:
        logger.info(f"Mock upload: {storage_key}")
        return storage_key

    try:
        # boto3 is synchronous; run it in a worker thread so the event loop
        # (and every other in-flight request) is not blocked during upload.
        await asyncio.to_thread(
            s3_client.put_object,
            Bucket=get_bucket_name(),
            Key=storage_key,
            Body=file_content,
            ContentType=content_type,
        )
        logger.info(f"Uploaded file to S3: {storage_key}")
        return storage_key
    except ClientError as e:
        logger.error("Failed to upload to S3: %s", type(e).__name__)
        raise ValueError("Failed to upload file") from e


async def generate_presigned_url(
    storage_key: str,
    expiration: int = 300,
    download_name: Optional[str] = None,
    content_type: Optional[str] = None,
) -> Optional[str]:
    """Generate a presigned URL for downloading a file.

    The URL forces an attachment download and a known content type so a stored
    text file is not opened inline as HTML.
    """
    s3_client = get_s3_client()
    if not s3_client:
        logger.warning("Presigned URL not available in mock mode")
        return None

    params = {"Bucket": get_bucket_name(), "Key": storage_key}
    if download_name:
        params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
    if content_type:
        params["ResponseContentType"] = content_type

    try:
        url = await asyncio.to_thread(
            s3_client.generate_presigned_url,
            "get_object",
            Params=params,
            ExpiresIn=expiration,
        )
        return url
    except ClientError as e:
        logger.error("Failed to generate presigned URL: %s", type(e).__name__)
        return None


async def delete_file_from_s3(storage_key: str) -> bool:
    """Delete a file from S3."""
    s3_client = get_s3_client()
    if not s3_client:
        logger.info(f"Mock delete: {storage_key}")
        return True

    try:
        await asyncio.to_thread(s3_client.delete_object, Bucket=get_bucket_name(), Key=storage_key)
        logger.info(f"Deleted file from S3: {storage_key}")
        return True
    except ClientError as e:
        logger.error(f"Failed to delete from S3: {e}")
        return False


async def ensure_bucket_exists():
    """Ensure the S3 bucket exists, create if needed."""
    s3_client = get_s3_client()
    if not s3_client:
        return False

    try:
        await asyncio.to_thread(s3_client.head_bucket, Bucket=get_bucket_name())
        return True
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code')
        if error_code == '404':
            try:
                if AWS_REGION == 'us-east-1':
                    await asyncio.to_thread(s3_client.create_bucket, Bucket=get_bucket_name())
                else:
                    await asyncio.to_thread(
                        s3_client.create_bucket,
                        Bucket=get_bucket_name(),
                        CreateBucketConfiguration={'LocationConstraint': AWS_REGION},
                    )
                logger.info(f"Created S3 bucket: {get_bucket_name()}")
                return True
            except ClientError as create_err:
                logger.error(f"Failed to create bucket: {create_err}")
                return False
        else:
            logger.error(f"Failed to check bucket: {e}")
            return False
