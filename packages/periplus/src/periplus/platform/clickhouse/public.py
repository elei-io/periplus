"""Setup-owned installation of the first complete public HTML/link capability."""
from importlib.resources import files
from periplus.platform.clickhouse.client import ClickHouseClient

PUBLIC_RELATIONS = frozenset({"capture", "html_element", "link", "page"})


def install_public_schema(client: ClickHouseClient, material: str = "material", database: str = "public_v1") -> None:
    import re
    from periplus.materialization.storage import material_database
    material_database(material)
    if not re.fullmatch(r"public_v1|query_[0-9a-f]{32}", database):
        raise ValueError("Invalid query namespace")
    source = files("periplus.platform.clickhouse").joinpath("public.sql").read_text().replace("material.", material + ".").replace("public_v1", database)
    for statement in source.split(";"):
        if statement.strip():
            client.execute(statement)
    for name in sorted(PUBLIC_RELATIONS):
        client.execute(f"SELECT * FROM {database}.{name} LIMIT 0")


def install_query_user(client: ClickHouseClient) -> None:
    from periplus.platform.clickhouse.client import ClickHouseConfig
    config = ClickHouseConfig.for_query()
    import re
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", config.username):
        raise ValueError("invalid query user name")
    client.execute(
        "CREATE USER IF NOT EXISTS {user:Identifier} IDENTIFIED WITH sha256_password BY {password:String} "
        "DEFAULT DATABASE public_v1 SETTINGS readonly=1, max_threads=2, max_memory_usage=536870912, "
        "max_execution_time=45, max_rows_to_read=10000000, max_bytes_to_read=1073741824",
        parameters={"user": config.username, "password": config.password.get_secret_value()},
    )
    client.execute(f"ALTER USER `{config.username}` SETTINGS readonly=1, "
        "max_execution_time=45 MIN 0.01 MAX 45 CHANGEABLE_IN_READONLY, "
        "max_threads=2 READONLY, max_memory_usage=536870912 READONLY, "
        "max_rows_to_read=10000000 READONLY, max_bytes_to_read=1073741824 READONLY, "
        "output_format_json_quote_64bit_integers=0 READONLY, "
        "cancel_http_readonly_queries_on_client_close=1 READONLY, "
        "timeout_before_checking_execution_speed=0 READONLY")
    client.execute(f"GRANT SELECT ON public_v1.* TO `{config.username}`")


def grant_query_target(client: ClickHouseClient, database: str) -> None:
    import re
    from periplus.platform.config import get_str
    if not re.fullmatch(r"public_v1|query_[0-9a-f]{32}", database):
        raise ValueError("Invalid query namespace")
    username = get_str("PERIPLUS_CLICKHOUSE_QUERY_USER")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", username):
        raise ValueError("Invalid query user name")
    client.execute(f"GRANT SELECT ON `{database}`.* TO `{username}`")
