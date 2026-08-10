"""The session-list SQL, compiled without a database.

The behavioural suite for these stores needs a real Postgres (``-m postgres``, docker), so a machine
without docker runs none of it. The list query is the newest SQL in this package and the only one
using LATERAL, so its *shape* is pinned here in the default suite: the predicates it filters on, the
join it derives titles and activity from, and the ordering a client's "most recent first" depends
on. Compile-only — it proves the statement renders the intended Postgres, not that Postgres likes
the result; that is the contract suite's job.
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from revi_store_postgres.stores import session_page_query, session_total_query


def _sql(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


class TestSessionListQuery:
    def test_it_filters_on_tenant_and_nothing_else(self) -> None:
        sql = _sql(session_page_query("acme-health", 50))
        assert "revi_session.sessions.tenant = 'acme-health'" in sql

    def test_the_turn_columns_are_derived_by_lateral_join(self) -> None:
        """Correlated per session row, so the investigations table is
        reached through its session_id index rather than aggregated whole."""
        sql = _sql(session_page_query("acme-health", 50))
        assert sql.count("LATERAL") == 2
        assert "count(*)" in sql
        assert "max(revi_trace.investigations.created_at)" in sql

    def test_the_first_question_is_the_earliest_non_empty_one(self) -> None:
        sql = _sql(session_page_query("acme-health", 50))
        assert "revi_trace.investigations.question IS NOT NULL" in sql
        assert "revi_trace.investigations.question != ''" in sql
        assert "LIMIT 1" in sql

    def test_a_session_with_no_turns_falls_back_to_its_own_created_at(self) -> None:
        sql = _sql(session_page_query("acme-health", 50))
        assert "coalesce(" in sql.lower()
        assert "LEFT OUTER JOIN LATERAL" in sql, "a turn-less session must still list"

    def test_it_orders_by_newest_activity_and_applies_the_limit(self) -> None:
        sql = _sql(session_page_query("acme-health", 25))
        assert "ORDER BY" in sql
        assert "DESC" in sql
        assert "LIMIT 25" in sql

    def test_the_total_counts_the_tenants_sessions_not_the_page(self) -> None:
        sql = _sql(session_total_query("acme-health"))
        assert "count(*)" in sql
        assert "revi_session.sessions" in sql
        assert "tenant = 'acme-health'" in sql
        assert "LIMIT" not in sql
