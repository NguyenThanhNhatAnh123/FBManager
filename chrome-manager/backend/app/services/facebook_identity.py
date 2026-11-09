"""Switch Facebook's rendered acting identity without reading session state."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from app.core.facebook import facebook_url

DOM_PATH=Path(__file__).with_name('facebook_identity_dom.js')
DIAGNOSTICS_ROOT=Path(__file__).resolve().parents[3]/'.runtime'/'facebook-identity'


class FacebookIdentityError(ValueError):
    pass


def _normal_name(value):
    return ' '.join((value or '').split()).casefold()


def _normal_url(value):
    if not value:return ''
    try:return facebook_url(value)[1].casefold()
    except (ValueError,TypeError):return ''


def _matches(candidate,expected_url=None,expected_name=None):
    url,name=_normal_url(candidate.get('url') or candidate.get('current_url')),_normal_name(candidate.get('name') or candidate.get('current_name'))
    wanted_url,wanted_name=_normal_url(expected_url),_normal_name(expected_name)
    if wanted_url and url:return url==wanted_url
    return bool(wanted_name and name==wanted_name)


async def _read(page):
    result=await page.evaluate('('+DOM_PATH.read_text(encoding='utf-8')+'\n)()')
    if result.get('blocked'):
        raise FacebookIdentityError('Facebook cần đăng nhập hoặc xác minh trước khi chuyển danh tính.')
    return result


async def _click(page,item):
    try:
        await page.locator('[data-cm-identity="'+str(item['id'])+'"]').click(timeout=5000)
    except asyncio.CancelledError:raise
    except Exception as error:
        try:await _capture_click_failure(page,item,error)
        except asyncio.CancelledError:raise
        except Exception:pass
        raise FacebookIdentityError('Không bấm được nút chuyển danh tính Facebook. Kiểm tra giao diện trong Chrome.') from None


def _click_error_category(error):
    # Inspect only in memory; never serialize browser call logs, locators,
    # document text, URLs or the raw exception into diagnostic files.
    message=str(error).casefold()
    if any(value in message for value in ('intercepts pointer events','intercepted','not receiving pointer events')):return 'pointer_intercepted'
    if any(value in message for value in ('has been closed','target closed','page closed','browser closed','context closed')):return 'closed'
    if any(value in message for value in ('detached','not attached','not found','no element','resolved to 0')):return 'detached_or_missing'
    if 'timeout' in type(error).__name__.casefold() or 'timeout' in message:return 'timeout'
    return 'other'


async def _capture_click_failure(page,item,error):
    parsed=urlsplit(page.url)
    if parsed.scheme!='https' or parsed.hostname not in ('facebook.com','www.facebook.com','m.facebook.com'):return
    allowed={'personal','page','unknown','menu','picker','switch_page','back','close','dismiss_tip'}
    kind=item.get('kind') if item.get('kind') in allowed else 'identity'
    DIAGNOSTICS_ROOT.mkdir(parents=True,exist_ok=True)
    picture=DIAGNOSTICS_ROOT/'click_failed.png'
    # A failed new capture must not leave an older screenshot paired with the
    # latest JSON failure category.
    picture.unlink(missing_ok=True)
    (DIAGNOSTICS_ROOT/'click_failed.json').write_text(json.dumps({'kind':kind,'error_category':_click_error_category(error)}),encoding='utf-8')
    await page.screenshot(path=str(picture),full_page=False,
                          mask=[page.locator('input,textarea,[contenteditable]')],timeout=5000)


async def _pause(page):
    await page.wait_for_timeout(500)


async def _ready(page,data=None):
    data=data if data is not None else await _read(page)
    for _ in range(20):
        if data.get('ready'):return data
        await _pause(page)
        data=await _read(page)
    raise FacebookIdentityError('Facebook chưa tải đủ giao diện để xác nhận danh tính. Chờ Chrome tải xong rồi tiếp tục.')


def _candidate(data,kind,expected_url=None,expected_name=None):
    values=[item for item in data.get('candidates',[]) if item.get('kind')!=('page' if kind=='personal' else 'personal')]
    if expected_url or expected_name:
        matches=[item for item in values if _matches(item,expected_url,expected_name)]
    else:
        matches=[item for item in values if item.get('kind')==kind]
    if len(matches)>1:
        raise FacebookIdentityError('Có nhiều danh tính Facebook trùng khớp. Cần chọn đúng tài khoản hoặc Page trước khi tiếp tục.')
    return matches[0] if matches else None


async def _close_menu(page,data):
    for kind in ('close','back'):
        item=next((item for item in data.get('controls',[]) if item.get('kind')==kind),None)
        if item:
            await _click(page,item)
            return
    # Escape dismisses menus only; it does not submit any Facebook form.
    try:await page.keyboard.press('Escape')
    except Exception:pass


async def _select_identity(page,data,kind,expected_url=None,expected_name=None):
    used=set();dismissed=0
    for _ in range(16):
        tip=next((item for item in data.get('controls',[]) if item.get('kind')=='dismiss_tip'),None)
        if tip:
            if dismissed>=3:
                raise FacebookIdentityError('Hướng dẫn trên Page chưa đóng được. Đóng hướng dẫn trong Chrome rồi tiếp tục chuyển danh tính.')
            await _click(page,tip);dismissed+=1
            await _pause(page)
            data=await _read(page)
            continue
        if kind=='page' and data.get('acting_page') and _matches(data,expected_url,expected_name):
            return {'kind':'page','name':data.get('current_name'),'url':data.get('current_url')},False
        target=_candidate(data,kind,expected_url,expected_name)
        if target:
            if target.get('selected'):
                await _close_menu(page,data)
                return target,False
            await _click(page,target)
            return target,True
        control=next((item for item in data.get('controls',[]) if item.get('kind')=='picker' and item.get('label') not in used),None)
        if control is None:
            control=next((item for item in data.get('controls',[]) if item.get('kind')=='menu' and item.get('label') not in used),None)
        if control is None:
            if not used:break
            await _pause(page)
            data=await _read(page)
            continue
        used.add(control.get('label'))
        await _click(page,control)
        await _pause(page)
        data=await _read(page)
    raise FacebookIdentityError('Chưa xác định được đúng danh tính cá nhân hoặc Page trong bộ chuyển tài khoản. Mở Chrome và chọn đúng danh tính rồi thử lại.')


async def ensure_personal_identity(page,expected_url=None,expected_name=None,*,require_switch=False):
    """Return to the bound personal identity before reading its managed Pages.

    No Page UI and no conflicting bound identity requires no switch. When Page
    UI is visible, choose only a unique bound or explicitly personal picker row.
    """
    data=await _ready(page)
    current={'url':data.get('current_url'),'name':data.get('current_name')}
    matches=_matches(current,expected_url,expected_name)
    identified=matches if expected_url or expected_name else bool(current['url'] and current['name'])
    if not data.get('acting_page') and not require_switch and identified:
        return {'kind':'personal','name':current['name'],'url':current['url'],'switched':False}
    target,switched=await _select_identity(page,data,'personal',expected_url,expected_name)
    stable=0;probed_me=False
    for _ in range(20):
        await _pause(page)
        current=await _read(page)
        if not current.get('acting_page') and current.get('ready'):
            if not probed_me:
                # Clicking a profile link can merely view it. Resolve /me only
                # after the picker settles so that the actor, not that link's
                # destination, supplies the identity we return.
                await page.wait_for_timeout(1000)
                try:await page.goto('https://www.facebook.com/me',wait_until='domcontentloaded',timeout=45000)
                except asyncio.CancelledError:raise
                except Exception:raise FacebookIdentityError('Chưa mở được trang cá nhân để xác nhận danh tính FB sau khi chuyển.') from None
                await _ready(page)
                probed_me=True;stable=0
                continue
            if current.get('current_url') and current.get('current_name') and (
                _matches(current,expected_url or target.get('url'),expected_name or target.get('name'))
                or target.get('kind')=='personal' and not (expected_url or expected_name)):
                stable+=1
                if stable>=2:return {'kind':'personal','name':current['current_name'],'url':current['current_url'],'switched':switched}
                continue
        stable=0
    raise FacebookIdentityError('Facebook chưa xác nhận đã chuyển về tài khoản cá nhân. Chưa đọc hoặc cập nhật danh sách Page.')


async def switch_to_page_identity(page,page_url,page_name=None):
    """Switch to one exact Page and verify rendered acting-Page evidence."""
    try:_,target_url=facebook_url(page_url)
    except (ValueError,TypeError):raise FacebookIdentityError('Cần URL Page Facebook hợp lệ để chuyển danh tính.') from None
    data=await _ready(page)
    if data.get('acting_page') and _matches(data,target_url,page_name):
        return {'kind':'page','name':data.get('current_name') or page_name,'url':target_url,'switched':False}
    if _normal_url(getattr(page,'url',''))!=_normal_url(target_url):
        try:await page.goto(target_url,wait_until='domcontentloaded',timeout=45000)
        except asyncio.CancelledError:raise
        except Exception:raise FacebookIdentityError('Chưa mở được Page để chuyển danh tính. Kiểm tra mạng hoặc phiên Facebook.') from None
        await _pause(page)
        data=await _ready(page)
    # Page navigation can expose a loaded header/account control before the
    # acting-Page sidebar arrives. Wait for that exact proof before considering
    # a picker; an explicit Switch now remains immediately actionable.
    for index in range(20):
        if data.get('acting_page') and _matches(data,target_url,page_name):
            return {'kind':'page','name':data.get('current_name') or page_name,'url':target_url,'switched':False}
        if any(item.get('kind')=='switch_page' for item in data.get('controls',[])):break
        if index<19:
            await _pause(page)
            data=await _read(page)
    button=next((item for item in data.get('controls',[]) if item.get('kind')=='switch_page'),None)
    if button:
        await _click(page,button)
        switched=True
    else:
        _,switched=await _select_identity(page,data,'page',target_url,page_name)
    for _ in range(20):
        await _pause(page)
        data=await _read(page)
        if data.get('acting_page') and _matches(data,target_url,page_name):
            return {'kind':'page','name':data.get('current_name') or page_name,'url':target_url,'switched':switched}
    raise FacebookIdentityError('Chưa xác nhận đã chuyển đúng Page. Không thực hiện thay đổi nội dung.')
