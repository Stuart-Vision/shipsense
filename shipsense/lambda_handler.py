"""AWS Lambda entry point: runs the pipeline for each file uploaded to the raw S3 bucket.

Ships upload files to s3://<raw bucket>/raw/...; the S3 ObjectCreated event
triggers this function, which downloads the file to /tmp and processes it.
"""

import tempfile
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote_plus

from .pipeline import connect, init_schema, process_file


@lru_cache(maxsize=1)
def s3_client():
    import boto3  # imported lazily: not needed for local runs or tests
    return boto3.client("s3")


def handler(event, context=None):
    results = []
    with connect() as conn:
        init_schema(conn)
        for record in event.get("Records", []):
            bucket = record["s3"]["bucket"]["name"]
            key = unquote_plus(record["s3"]["object"]["key"])  # S3 URL-encodes keys in events
            with tempfile.TemporaryDirectory() as tmp:
                local = Path(tmp) / Path(key).name
                s3_client().download_file(bucket, key, str(local))
                results.append(process_file(local, conn, source=f"s3://{bucket}/{key}"))
    if any(r["status"] == "failed" for r in results):
        # Raising makes Lambda retry the event and, after that, send it to the failure destination.
        raise RuntimeError(f"pipeline failed: {results}")
    return {"results": results}
