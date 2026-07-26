"""Shared function classes with compiler-proven execution properties."""

from sqlglot import exp

DETERMINISTIC_ROW_LOCAL_FUNCTION_TYPES = (
    exp.Abs,
    exp.And,
    exp.Array,
    exp.ArrayAppend,
    exp.ArrayToString,
    exp.Case,
    exp.Cast,
    exp.Ceil,
    exp.Coalesce,
    exp.Concat,
    exp.ConcatWs,
    exp.EndsWith,
    exp.Extract,
    exp.Floor,
    exp.Greatest,
    exp.If,
    exp.Least,
    exp.Left,
    exp.Length,
    exp.Lower,
    exp.Not,
    exp.Nullif,
    exp.Or,
    exp.RegexpExtract,
    exp.RegexpFullMatch,
    exp.RegexpLike,
    exp.RegexpReplace,
    exp.Replace,
    exp.Right,
    exp.Round,
    exp.SHA2,
    exp.Split,
    exp.SplitPart,
    exp.StartsWith,
    exp.StrPosition,
    exp.Struct,
    exp.Substring,
    exp.TimestampTrunc,
    exp.Trim,
    exp.Try,
    exp.TryCast,
    exp.Upper,
)

DETERMINISTIC_DUCKDB_FUNCTION_NAMES = frozenset(
    {
        "list_slice",
        "list_value",
        "map_contains",
        "map_entries",
        "map_extract_value",
        "struct_pack",
        "substring",
        "trim",
        "url_decode",
        "url_encode",
    }
)

VOLATILE_FUNCTION_TYPES = (
    exp.CurrentDate,
    exp.CurrentDatetime,
    exp.CurrentTime,
    exp.CurrentTimestamp,
    exp.CurrentUser,
    exp.NextValueFor,
    exp.Rand,
    exp.SessionUser,
    exp.Uuid,
)

VOLATILE_FUNCTION_NAMES = frozenset(
    {
        "currval",
        "error",
        "gen_random_uuid",
        "nextval",
        "now",
        "setval",
        "today",
    }
)
