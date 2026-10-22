"""Text-to-SQL guard (prototype of the rules; production runs the same parser, libpg_query, from the Vercel route).
Accepts exactly one SELECT over the documented semantic views, using only allow-listed node types, functions
and casts, and returns it re-printed from the parse tree (comments and anything after the statement are gone)
wrapped in an outer LIMIT."""
from __future__ import annotations

from pglast import ast, parse_sql, stream
from pglast.parser import ParseError

VIEWS = {"policy_meetings", "policy_votes", "policy_projections", "market_daily"}   # grows per module
FUNCS = {
    # aggregates and statistics
    "count", "sum", "avg", "min", "max", "stddev", "stddev_samp", "stddev_pop", "variance", "var_samp", "var_pop",
    "percentile_cont", "percentile_disc", "corr", "covar_pop", "covar_samp", "regr_slope", "regr_intercept",
    "string_agg", "array_agg", "bool_and", "bool_or",
    # window functions
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead",
    "first_value", "last_value", "nth_value",
    # maths
    "round", "abs", "ceil", "ceiling", "floor", "trunc", "sqrt", "power", "ln", "log", "exp", "sign", "mod",
    # dates and text
    "date_trunc", "date_part", "extract", "age", "make_date", "to_char", "to_date", "now",
    "lower", "upper", "initcap", "length", "concat", "left", "right", "replace", "split_part", "position",
    "substr", "substring", "btrim", "ltrim", "rtrim",     # trim(...) parses as btrim/ltrim/rtrim (Review 01, F11)
}
CAST_TYPES = {"numeric", "int2", "int4", "int8", "integer", "bigint", "smallint", "float4", "float8", "real",
              "text", "varchar", "date", "timestamp", "timestamptz", "interval", "bool", "boolean"}
NODES = {  # parse-tree node types a read-only analytical query needs; anything else is refused
    "SelectStmt", "ResTarget", "ColumnRef", "A_Star", "A_Const", "A_Expr", "BoolExpr", "NullTest", "BooleanTest",
    "FuncCall", "TypeCast", "TypeName", "SortBy", "WindowDef", "RangeVar", "JoinExpr", "RangeSubselect",
    "SubLink", "CaseExpr", "CaseWhen", "CoalesceExpr", "MinMaxExpr", "WithClause", "CommonTableExpr",
    "Alias", "String", "Integer", "Float", "Boolean", "SQLValueFunction", "A_Indirection", "A_Indices",
    "RowExpr", "GroupingSet", "ParamRef", "A_ArrayExpr", "CollateClause",
}
MAX_ROWS = 200


class Rejected(Exception):
    pass


def _walk(node, cte_names):
    if isinstance(node, (list, tuple)):
        for n in node:
            _walk(n, cte_names)
        return
    if not isinstance(node, ast.Node):
        return
    kind = type(node).__name__
    if kind not in NODES:
        raise Rejected(f"construct not allowed: {kind}")
    if kind == "SelectStmt":
        if node.intoClause is not None:
            raise Rejected("SELECT INTO not allowed")
        if node.lockingClause:
            raise Rejected("locking clause not allowed")
        if node.withClause is not None:
            if node.withClause.recursive:   # unbounded row generation, like generate_series (Review 01, F11)
                raise Rejected("recursive CTE not allowed")
            for cte in node.withClause.ctes:
                cte_names.add(cte.ctename)
    if kind == "RangeVar":
        if node.catalogname:
            raise Rejected("database-qualified names not allowed")
        # compare exactly: Postgres has already folded unquoted names to lower case, and a quoted name
        # keeps its case, so "Policy_Meetings" is a different (non-existent) relation
        schema = node.schemaname or ""
        name = node.relname
        if not ((schema in ("", "semantic") and name in VIEWS) or (schema == "" and name in cte_names)):
            raise Rejected(f"relation not allowed: {schema + '.' if schema else ''}{name}")
    if kind == "FuncCall":
        parts = [p.sval for p in node.funcname]
        if len(parts) > 2 or (len(parts) == 2 and parts[0] != "pg_catalog") or parts[-1] not in FUNCS:
            raise Rejected(f"function not allowed: {'.'.join(parts)}")
    if kind == "TypeCast":
        tname = node.typeName.names[-1].sval
        if tname not in CAST_TYPES:
            raise Rejected(f"cast not allowed: {tname}")
    if kind == "SQLValueFunction" and node.op.name not in ("SVFOP_CURRENT_DATE", "SVFOP_CURRENT_TIMESTAMP"):
        raise Rejected("only CURRENT_DATE and CURRENT_TIMESTAMP are allowed")
    for attr in node:                       # iterate child fields
        _walk(getattr(node, attr), cte_names)


def guard(sql: str, max_rows: int = MAX_ROWS) -> str:
    try:
        stmts = parse_sql(sql)
    except ParseError as e:
        raise Rejected(f"does not parse: {e}") from None
    if len(stmts) != 1:
        raise Rejected("exactly one statement allowed")
    stmt = stmts[0].stmt
    if not isinstance(stmt, ast.SelectStmt):
        raise Rejected(f"only SELECT allowed, got {type(stmt).__name__}")
    _walk(stmt, set())
    return f"SELECT * FROM ({stream.RawStream()(stmt)}) AS q LIMIT {int(max_rows)}"
