"""SQLAlchemy dialect for ``sqlite+pysqlcipher``.

pyrekordbox opens ``exportLibrary.db`` with that URL. SQLAlchemy 2 no longer
ships the dialect. This is a small adaptation of SQLAlchemy 1.4's
``sqlite+pysqlcipher`` dialect (MIT), enough to send ``PRAGMA key`` before any
other statement. It does not write to the player.
"""

from __future__ import annotations

from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite
from sqlalchemy.pool import SingletonThreadPool


class SQLiteDialect_pysqlcipher(SQLiteDialect_pysqlite):
    driver = "pysqlcipher"
    supports_statement_cache = True
    pragmas = ("kdf_iter", "cipher", "cipher_page_size", "cipher_use_hmac")

    @classmethod
    def import_dbapi(cls):
        from sqlcipher3 import dbapi2

        return dbapi2

    @classmethod
    def get_pool_class(cls, url):
        return SingletonThreadPool

    def create_connect_args(self, url):
        self._passphrase = url.password or ""
        extra = []
        for name in self.pragmas:
            if name in url.query:
                extra.append((name, url.query[name]))
        self._extra_pragmas = extra
        plain = url._replace(password=None).difference_update_query(list(self.pragmas))
        return super().create_connect_args(plain)

    def on_connect(self):
        parent = super().on_connect()
        passphrase = getattr(self, "_passphrase", "") or ""
        extra = list(getattr(self, "_extra_pragmas", ()) or ())

        def _quote(value: str) -> str:
            return str(value).replace('"', '""')

        def connect(dbapi_connection):
            cursor = dbapi_connection.cursor()
            cursor.execute('PRAGMA key="%s"' % _quote(passphrase))
            for name, value in extra:
                cursor.execute('PRAGMA %s="%s"' % (name, _quote(value)))
            cursor.close()
            if parent is not None:
                parent(dbapi_connection)

        return connect


def register() -> None:
    """Teach SQLAlchemy the ``sqlite+pysqlcipher`` URL pyrekordbox uses."""
    from sqlalchemy.dialects import registry

    registry.register(
        "sqlite.pysqlcipher",
        "prolink.pysqlcipher_dialect",
        "SQLiteDialect_pysqlcipher",
    )
