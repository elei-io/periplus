"""Local Compose only: provision least-privilege lake reader identities."""
import json
import os
import urllib.request
import urllib.error
from urllib.parse import quote
from xml.etree import ElementTree as ET

import boto3
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
import psycopg
from psycopg import sql


def main():
    reader = os.environ["LAKE_QUERY_USER"]
    with psycopg.connect(os.environ["LAKE_ADMIN_DSN"], autocommit=True) as d:
        if not d.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (reader,)).fetchone():
            d.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(reader)))
        d.execute(sql.SQL("ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(reader), sql.Literal(os.environ["LAKE_QUERY_PASSWORD"])))
        d.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only=on").format(sql.Identifier(reader)))
        d.execute(sql.SQL("ALTER ROLE {} SET statement_timeout='20s'").format(sql.Identifier(reader)))
        d.execute(sql.SQL("GRANT USAGE ON SCHEMA ducklake TO {}").format(sql.Identifier(reader)))
        d.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA ducklake TO {}").format(sql.Identifier(reader)))
        d.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA ducklake GRANT SELECT ON TABLES TO {}").format(sql.Identifier(reader)))
    endpoint = os.environ["LAKE_S3_ENDPOINT"]
    access, secret = os.environ["LAKE_S3_KEY_ID"], os.environ["LAKE_S3_SECRET_ACCESS_KEY"]
    account = ET.Element("Account")
    for name, value in {"Access": os.environ["LAKE_QUERY_S3_KEY_ID"], "Secret": os.environ["LAKE_QUERY_S3_SECRET_ACCESS_KEY"], "Role": "user"}.items():
        ET.SubElement(account, name).text = value
    def admin(path):
        req = AWSRequest(method="PATCH", url=endpoint+path, data=ET.tostring(account), headers={"Content-Type": "application/xml"})
        S3SigV4Auth(Credentials(access, secret), "s3", "us-east-1").add_auth(req)
        with urllib.request.urlopen(urllib.request.Request(req.url, data=req.body, headers=dict(req.headers), method="PATCH"), timeout=10) as response:
            response.read()
    try:
        admin('/create-user')
    except urllib.error.HTTPError as e:
        if ET.fromstring(e.read()).findtext('Code') != 'XAdminUserExists':
            raise
        account.tag = 'MutableProps'
        admin('/update-user?access=' + quote(os.environ['LAKE_QUERY_S3_KEY_ID'], safe=''))
    s3 = boto3.client('s3', endpoint_url=endpoint, region_name='us-east-1', aws_access_key_id=access, aws_secret_access_key=secret)
    s3.put_bucket_policy(Bucket='lake', Policy=json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"AWS": [os.environ['LAKE_QUERY_S3_KEY_ID']]}, "Action": ["s3:GetObject"], "Resource": ["arn:aws:s3:::lake/*"]}]}))
    print('Query reader identities provisioned.')


if __name__ == '__main__':
    main()
