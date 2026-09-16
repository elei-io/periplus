"""Record physical bytes once, and index/projection/merge components separately."""

import json

from client import ARTIFACTS, DATABASE, data, literal


def record() -> None:
    database = literal(DATABASE)
    queries = {
        "parts": f"SELECT table,sum(rows) rows,count() parts,sum(bytes_on_disk) bytes_on_disk,sum(data_compressed_bytes) column_bytes,sum(data_uncompressed_bytes) uncompressed_column_bytes FROM system.parts WHERE database={database} AND active GROUP BY table ORDER BY table",
        "indexes": f"SELECT table,name,expr,type,data_compressed_bytes,data_uncompressed_bytes,marks_bytes FROM system.data_skipping_indices WHERE database={database} ORDER BY table,name",
        "projections": f"SELECT table,name,sum(bytes_on_disk) bytes_on_disk,sum(rows) rows,count() parts FROM system.projection_parts WHERE database={database} AND active GROUP BY table,name ORDER BY table,name",
        "projection_columns": f"SELECT table,name,column,sum(column_data_compressed_bytes) bytes FROM system.projection_parts_columns WHERE database={database} AND active GROUP BY table,name,column ORDER BY table,name,column",
        "merges": f"""SELECT table,count() completed_merges,sum(duration_ms) duration_ms,sum(read_rows) read_rows,sum(read_bytes) read_bytes,sum(size_in_bytes) output_bytes,max(peak_memory_usage) peak_memory_usage,
        sum(ProfileEvents['UserTimeMicroseconds']+ProfileEvents['SystemTimeMicroseconds']) cpu_microseconds,
        sum(ProfileEvents['S3PutObject']) s3_puts,sum(ProfileEvents['S3GetObject']) s3_gets,
        sum(ProfileEvents['S3WriteRequestsCount']) s3_write_requests,
        sum(ProfileEvents['S3CreateMultipartUpload']) multipart_creates,
        sum(ProfileEvents['WriteBufferFromS3Bytes']) s3_written_bytes,
        countIf(error!=0) errors
        FROM system.part_log WHERE database={database} AND event_type='MergeParts' GROUP BY table ORDER BY table""",
    }
    result = {
        name: data(sql, read=False, row_limit=10000, seconds=120)
        for name, sql in queries.items()
    }
    (ARTIFACTS / "physical-storage.json").write_text(json.dumps(result, indent=2))
    print("Recorded physical bytes, index/projection components and completed merges")


if __name__ == "__main__":
    record()
