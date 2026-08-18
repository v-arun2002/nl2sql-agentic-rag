"""
SQL syntax highlighting for the Streamlit trace panel.

Kept separate from ui/app.py because that module executes the whole Streamlit
app at import time (st.set_page_config, disk scans, page layout). This function
is pure, so it lives where it can be imported and unit-tested on its own.
"""

import html
import re

SQL_KEYWORDS = (
    "WITH|SELECT|DISTINCT|FROM|WHERE|INNER JOIN|LEFT JOIN|OUTER JOIN|JOIN|ON|USING"
    "|GROUP BY|ORDER BY|HAVING|LIMIT|OFFSET|AS|AND|OR|NOT|NULL|IS|IN|EXISTS|BETWEEN|LIKE"
    "|CASE|WHEN|THEN|ELSE|END|UNION ALL|UNION|ASC|DESC|COUNT|SUM|AVG|MAX|MIN|CAST|SUBSTR"
    "|COALESCE|NULLIF|IIF|STRFTIME|ROUND|ABS|REAL|INTEGER|FLOAT|TEXT|OVER|PARTITION BY|RANK"
)
SQL_TOKEN = re.compile(
    r"(?P<comment>--[^\n]*)"
    r"|(?P<str>'[^']*')"
    r"|(?P<kw>\b(?:" + SQL_KEYWORDS + r")\b)"
    r"|(?P<num>\b\d+(?:\.\d+)?\b)",
    re.IGNORECASE,
)


def highlight_sql(sql: str) -> str:
    """
    Rendered in-house rather than with st.code, which ships its own light theme
    and ignores the surrounding palette. quote=False keeps single quotes intact
    so the string-literal pattern still matches; &, < and > are still escaped,
    and spans are only added afterwards.
    """
    escaped = html.escape(sql or "", quote=False)

    def sub(m):
        for kind in ("comment", "str", "kw", "num"):
            if m.group(kind):
                return '<span class="sql-%s">%s</span>' % (kind, m.group(kind))
        return m.group(0)

    return SQL_TOKEN.sub(sub, escaped)
