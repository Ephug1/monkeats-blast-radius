"""Fallback for coverage collection on machines without DuckDB.
Permit fixture setup only. Any DB query fails, marking its test incomplete.
"""
class SetupConnection:
    def execute(self, sql, *args, **kwargs):
        if sql.lstrip().upper().startswith(("CREATE VIEW ", "CREATE TABLE ")):
            return self
        raise RuntimeError("DuckDB unavailable during local coverage collection")

    def close(self):
        pass

def connect(*args, **kwargs):
    return SetupConnection()
