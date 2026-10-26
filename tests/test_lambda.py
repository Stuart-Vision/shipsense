"""The Lambda entry point, with a fake S3 client standing in for AWS."""

import shutil

import pytest

from shipsense import lambda_handler


class FakeS3:
    def __init__(self, files):
        self.files = files
        self.downloads = []

    def download_file(self, bucket, key, dest):
        self.downloads.append((bucket, key))
        shutil.copy(self.files[key], dest)


def s3_event(bucket, *keys):
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": k}}} for k in keys]}


def test_handler_processes_each_uploaded_file(conn, sample_files, monkeypatch):
    fake = FakeS3({"raw/MV-ORION/ME 1.csv": sample_files["ME-1"], "raw/MV-ORION/gen.jsonl": sample_files["GEN-1"]})
    monkeypatch.setattr(lambda_handler, "s3_client", lambda: fake)
    monkeypatch.setenv("DATABASE_URL", conn.info.dsn + " password=" + (conn.info.password or ""))

    # S3 URL-encodes keys in events ("ME 1.csv" arrives as "ME+1.csv").
    result = lambda_handler.handler(s3_event("ship-raw", "raw/MV-ORION/ME+1.csv", "raw/MV-ORION/gen.jsonl"))

    assert [r["status"] for r in result["results"]] == ["succeeded", "succeeded"]
    assert result["results"][0]["source"] == "s3://ship-raw/raw/MV-ORION/ME 1.csv"
    assert fake.downloads[0] == ("ship-raw", "raw/MV-ORION/ME 1.csv")


def test_handler_raises_on_failure_so_lambda_retries(conn, tmp_path, monkeypatch):
    bad = tmp_path / "bad.csv"
    bad.write_text("nonsense\n1\n", encoding="utf-8")
    monkeypatch.setattr(lambda_handler, "s3_client", lambda: FakeS3({"raw/bad.csv": bad}))
    monkeypatch.setenv("DATABASE_URL", conn.info.dsn + " password=" + (conn.info.password or ""))

    with pytest.raises(RuntimeError, match="pipeline failed"):
        lambda_handler.handler(s3_event("ship-raw", "raw/bad.csv"))


def test_database_url_comes_from_secrets_manager_on_aws(monkeypatch):
    import sys
    import types

    from shipsense.pipeline import database_url

    class FakeSecrets:
        def get_secret_value(self, SecretId):
            assert SecretId == "arn:aws:secretsmanager:ap-southeast-1:123:secret:db"
            return {"SecretString": '{"url": "postgresql://app@rds-host:5432/shipsense"}'}

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda name: FakeSecrets()))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_SECRET_ARN", "arn:aws:secretsmanager:ap-southeast-1:123:secret:db")

    assert database_url() == "postgresql://app@rds-host:5432/shipsense"
