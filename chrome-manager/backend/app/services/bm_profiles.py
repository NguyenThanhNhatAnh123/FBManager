"""Local BM profile classification and notes; no Meta permissions or Page changes."""
import json
from sqlalchemy import select
from app.models.entities import BmProfile,BmProxy,BmBrowserSettings,Profile,now


def public_bm_profiles(session):
    mobile={row.profile_id:row.mobile_enabled for row in session.scalars(select(BmBrowserSettings))}
    return {row.profile_id:{'portfolios':json.loads(row.portfolios),'note':row.note,'updated_at':row.updated_at,'mobile_enabled':mobile.get(row.profile_id,False)}
            for row in session.scalars(select(BmProfile))}


def save_bm_mobile(session,profile_id,enabled):
    row=session.get(BmBrowserSettings,profile_id)
    if row is None:
        row=BmBrowserSettings(profile_id=profile_id)
        session.add(row)
    row.mobile_enabled=enabled
    return row


def save_bm_profile(session,profile_id,body):
    row=session.get(BmProfile,profile_id)
    if row is None:
        row=BmProfile(profile_id=profile_id)
        session.add(row)
    row.portfolios=json.dumps([portfolio.model_dump() for portfolio in body.portfolios],ensure_ascii=False)
    row.note,row.updated_at=body.note,now()
    adopt_bm_proxy(session,profile_id)
    return row


def adopt_bm_proxy(session,profile_id):
    profile=session.get(Profile,profile_id)
    if profile is not None and profile.proxy_id:
        scope=session.get(BmProxy,profile.proxy_id)
        if scope is None:
            session.add(BmProxy(proxy_id=profile.proxy_id,profile_id=profile_id))
        elif scope.profile_id in (None,profile_id):
            scope.profile_id=profile_id
        else:
            raise ValueError('Proxy BM đã gán cho profile khác.')


def adopt_existing_bm_proxies(db):
    """Add scope metadata for existing BM profiles, preserving their proxy and saved sessions."""
    with db.session.begin() as session:
        for profile_id in session.scalars(select(BmProfile.profile_id)):
            adopt_bm_proxy(session,profile_id)
