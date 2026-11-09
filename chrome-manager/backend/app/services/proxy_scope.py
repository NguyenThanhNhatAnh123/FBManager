"""Read-only proxy-pool filtering for offline scripts, including older databases."""


def ordinary_proxy_where(connection):
    """Return a WHERE clause for an unaliased proxies table without migrating it.

    BM roles can predate the dedicated-scope table. Hide both their current
    assignments and permanent scope rows, so offline checks work before startup
    has adopted those assignments and after private proxies have been detached.
    """
    tables={row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('profiles','bm_profiles','bm_proxies')")}
    conditions=[]
    if 'bm_proxies' in tables:
        conditions.append('id NOT IN (SELECT proxy_id FROM bm_proxies WHERE proxy_id IS NOT NULL)')
    if {'profiles','bm_profiles'}<=tables:
        conditions.append('id NOT IN (SELECT p.proxy_id FROM profiles p JOIN bm_profiles b ON b.profile_id=p.id WHERE p.proxy_id IS NOT NULL)')
    return ' WHERE '+' AND '.join(conditions) if conditions else ''
