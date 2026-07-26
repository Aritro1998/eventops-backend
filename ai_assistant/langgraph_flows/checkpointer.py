from django.db import connections

from psycopg_pool import ConnectionPool
from langgraph.checkpoint.postgres import PostgresSaver


def get_postgres_dsn():
    db = connections["default"].settings_dict
    return f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"


_pool = None
_checkpointer = None


def get_checkpointer():
    """
    Lazily builds the pool/checkpointer on first real use rather than at
    import time. Django's test runner imports every test module (to
    discover TestCase classes) before it creates the test database and
    swaps the connection name to the disposable test one - resolving the
    DSN at import time would permanently capture the real database name,
    before the test database even exists.
    """
    global _pool, _checkpointer
    if _checkpointer is None:
        # min_size=0 so idle connections actually close instead of sitting
        # open forever - otherwise Django's test runner can't drop the
        # disposable test database afterward, since our pool is still
        # holding sessions open against it.
        _pool = ConnectionPool(conninfo=get_postgres_dsn(), min_size=0, max_size=10, kwargs={"autocommit": True})
        _checkpointer = PostgresSaver(_pool)
        _checkpointer.setup()
    return _checkpointer


def close_checkpointer():
    """Close the pool and forget the cached checkpointer, so the next
    get_checkpointer() call builds a fresh one against whatever database
    is live at that point. Needed at the end of a test run - see
    get_checkpointer's docstring for why the pool can otherwise block
    Django from dropping the test database."""
    global _pool, _checkpointer
    if _pool is not None:
        _pool.close()
    _pool = None
    _checkpointer = None