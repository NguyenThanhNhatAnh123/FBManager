"""Read fresh Facebook security codes from the rendered, signed-in Outlook UI.

This reader never signs in, sends/resends codes, follows message links, or writes
mail contents/codes to storage. The caller owns the profile and its browser lock.
"""
import asyncio
import hashlib
import inspect
import math
import re
import time
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Error as BrowserError,TimeoutError as BrowserTimeoutError

from app.services.outlook import AUTH_HOSTS,OUTLOOK_URL,is_outlook_landing,navigation_in_progress


DOM_PATH=Path(__file__).with_name('outlook_inbox_dom.js')
DOM_SOURCE=DOM_PATH.read_text(encoding='utf-8')
MAIL_HOSTS={'outlook.live.com','outlook.office.com','outlook.office365.com'}
SECURITY_SENDER='security@facebookmail.com'
POLL_SECONDS=12
DIAGNOSTIC_FLAGS=('sender_verified','recipient_verified','time_parsed','time_fresh','body_present','matching_code_context')
EMAIL_RE=re.compile(r'[A-Z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,63}',re.I)
CODE_RE=re.compile(r'(?<![\w])[0-9]{6,8}(?![\w])')
CODE_WORDS=re.compile(r'\b(?:security|confirmation|verification|login|log.in|password.reset|authentication)?\s*code\b|mã\s+(?:bảo mật|xác nhận|xác minh|đăng nhập|đặt lại)|code\s+de\s+(?:sécurité|confirmation|vérification)|bestätigungscode|sicherheitscode',re.I)
IRRELEVANT_SUBJECT=re.compile(r'\binvit(?:e|ed|ation)\b|\blogin[ -]alert\b|\bnew login\b|\bsign.in alert\b|did you (?:just )?log in|\bads?\b|\badvertis|\bcampaign\b|lời mời|cảnh báo đăng nhập|đăng nhập mới',re.I)
MARKER_RE=re.compile(r'^\[data-cm-outlook-(?:account|folder|tab|message|details)="[0-9]{1,3}"\]$')


class OutlookInboxError(ValueError):
    pass


class OutlookInboxNeedsLogin(OutlookInboxError):
    """The expected Outlook mailbox needs a verified signed-in session."""


class OutlookInboxWrongAccount(OutlookInboxNeedsLogin):
    """A different account was explicitly visible in Outlook's own UI."""


def _address(value):
    matches=EMAIL_RE.findall(str(value or ''))
    return matches[0].casefold() if len(matches)==1 else None


def _aware_time(value):
    if isinstance(value,datetime):result=value
    elif isinstance(value,str):
        try:result=datetime.fromisoformat(value.strip().replace('Z','+00:00'))
        except ValueError:
            try:result=parsedate_to_datetime(value)
            except (ValueError,TypeError,OverflowError):return None
    else:return None
    return result.astimezone(timezone.utc) if result.tzinfo is not None else None


def _utc_now():
    return datetime.now(timezone.utc)


def _received_time(message):
    value=message.get('received_at')
    aware=_aware_time(value)
    if aware is not None:return aware
    # A local full date is usable only with its rendered browser Date offset.
    # JS getTimezoneOffset is UTC-local, the inverse of Python's UTC offset.
    offset=message.get('received_timezone_offset_minutes')
    if type(offset) not in (int,float) or not math.isfinite(offset) or abs(offset)>840:return None
    match=re.fullmatch(r'(?:[A-Za-z]{3,9}\s+)?(20\d{2})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?',str(value or '').strip())
    if match is None:return None
    try:
        parts=[int(value or 0) for value in match.groups()]
        local=datetime(*parts,tzinfo=timezone(timedelta(minutes=-offset)))
        return local.astimezone(timezone.utc)
    except (ValueError,OverflowError):return None


def _message_fingerprint(message):
    key=str(message.get('key') or '')
    subject=re.sub(r'\s+',' ',str(message.get('subject') or '')).strip().casefold()
    # Subject is necessary: Outlook can reuse a conversation ID for a new code.
    return hashlib.sha256((key+'\0'+subject).encode()).hexdigest()


def _key_fingerprint(message):
    return hashlib.sha256(str(message.get('key') or '').encode()).hexdigest()


def _code_hash(code):
    return hashlib.sha256(code.encode('ascii')).hexdigest()


def _safe_diagnostics(message,expected_email,since,*,allow_minute_rounding=False,first_seen_at=None):
    result=dict.fromkeys(DIAGNOSTIC_FLAGS,False)
    if not isinstance(message,dict):return result
    result['sender_verified']=not bool(message.get('unverified')) and _address(message.get('sender'))==SECURITY_SENDER
    recipients=message.get('recipients')
    result['recipient_verified']=isinstance(recipients,(list,tuple)) and _address(expected_email) in {_address(value) for value in recipients}
    received,requested,current=_received_time(message),_aware_time(since),_utc_now()
    result['time_parsed']=received is not None
    if received and requested and received<=current:
        first_seen=_aware_time(first_seen_at)
        rounded=allow_minute_rounding and message.get('received_precision')=='minute' and first_seen is not None and first_seen>=requested and requested-received<=timedelta(seconds=60)
        result['time_fresh']=received>=requested or bool(rounded)
    body=str(message.get('body') or '')[:30000]
    subject=str(message.get('subject') or '')[:1000]
    result['body_present']=bool(body.strip())
    clean_body=re.sub(r'https?://\S+|www\.\S+',' ',body,flags=re.I)
    text=re.sub(r'https?://\S+|www\.\S+',' ',subject,flags=re.I)+'\n'+clean_body
    result['matching_code_context']=bool(not IRRELEVANT_SUBJECT.search(subject) and CODE_WORDS.search(subject) and CODE_WORDS.search(clean_body) and re.search(r'\bfacebook\b',text,re.I) and len(set(CODE_RE.findall(text)))==1)
    return {key:bool(value) for key,value in result.items()}


def extract_facebook_code(message,expected_email,since,*,now=None,used_code_hashes=(),allow_minute_rounding=False,first_seen_at=None):
    """Return one fresh, recipient-bound Facebook code, otherwise None.

    Header fields must come from Outlook's message chrome, never inferred from
    the message body or the signed-in mailbox alone.
    """
    if not isinstance(message,dict) or message.get('unverified'):
        return None
    expected=_address(expected_email)
    if not expected or _address(message.get('sender'))!=SECURITY_SENDER:
        return None
    recipients=message.get('recipients')
    if not isinstance(recipients,(list,tuple)) or expected not in {_address(value) for value in recipients}:
        return None
    requested=_aware_time(since)
    received=_received_time(message)
    current=_aware_time(now or _utc_now())
    if not requested or not received or not current or received>current:
        return None
    if received<requested:
        first_seen=_aware_time(first_seen_at)
        rounding=allow_minute_rounding and message.get('received_precision')=='minute'
        if not rounding or first_seen is None or first_seen<requested or requested-received>timedelta(seconds=60):
            return None
    subject=str(message.get('subject') or '')[:1000]
    body=str(message.get('body') or '')[:30000]
    if IRRELEVANT_SUBJECT.search(subject) or not CODE_WORDS.search(subject):
        return None
    # Strip links before looking for digits: IDs in URL paths/query strings are
    # not security codes. Facebook must also be named in the rendered content.
    clean_body=re.sub(r'https?://\S+|www\.\S+',' ',body,flags=re.I)
    text=re.sub(r'https?://\S+|www\.\S+',' ',subject,flags=re.I)+'\n'+clean_body
    if not re.search(r'\bfacebook\b',text,re.I) or not CODE_WORDS.search(clean_body):
        return None
    candidates=set(CODE_RE.findall(text))
    if len(candidates)!=1:
        return None
    code=next(iter(candidates))
    return None if _code_hash(code) in used_code_hashes else code


class OutlookInboxReader:
    def __init__(self,page,email,notify=None,*,require_account=True):
        expected=_address(email)
        if expected is None or expected!=str(email).strip().casefold():
            raise OutlookInboxError('Email Outlook không hợp lệ.')
        self.page,self.email,self.notify=page,expected,notify
        self.require_account=bool(require_account)
        self.prepared=False
        self._used_code_hashes=set()
        self._consumed_messages=set()
        self._baseline_messages=set()
        self._baseline_unknown_keys=set()
        self._baseline_captured_at=None
        self._baseline_complete=False
        self._first_seen={}
        self._diagnostics=dict.fromkeys(DIAGNOSTIC_FLAGS,False)

    @property
    def diagnostics(self):
        """Only safe boolean flags from the most relevant inspected message."""
        return dict(self._diagnostics)

    async def _notify(self,message,remaining):
        if self.notify is None:return
        try:
            result=self.notify(message,max(0,math.ceil(remaining)))
            if inspect.isawaitable(result):await result
        except Exception:
            pass  # An optional progress display cannot interrupt mailbox reads.

    def _host(self):
        try:
            parsed=urlsplit(self.page.url)
            if parsed.scheme=='https' and not parsed.username and not parsed.password and parsed.port in (None,443):
                return (parsed.hostname or '').casefold()
        except (ValueError,TypeError):pass
        return ''

    async def _snapshot(self):
        host=self._host()
        if host in AUTH_HOSTS or is_outlook_landing(self.page.url):
            raise OutlookInboxNeedsLogin('Cần đăng nhập đúng hộp thư Outlook trước khi đọc mã Facebook.')
        if host not in MAIL_HOSTS:
            raise OutlookInboxError('Tab đọc thư không ở website Outlook hợp lệ.')
        try:
            data=await self.page.evaluate(DOM_PATH.read_text(encoding='utf-8'),{'email':self.email})
        except OSError:
            raise OutlookInboxError('Không đọc được bộ nhận diện giao diện Outlook. Kiểm tra ứng dụng rồi thử lại.') from None
        except BrowserError as error:
            if isinstance(error,BrowserTimeoutError) or navigation_in_progress(error):return {'state':'loading'}
            raise OutlookInboxError('Không đọc được giao diện Outlook. Kiểm tra tab hộp thư rồi thử lại.') from None
        if not isinstance(data,dict):return {'state':'loading'}
        if data.get('state')=='login':
            raise OutlookInboxNeedsLogin('Cần đăng nhập đúng hộp thư Outlook trước khi đọc mã Facebook.')
        account=_address(data.get('account_email'))
        if account and account!=self.email:
            raise OutlookInboxWrongAccount('Outlook đang đăng nhập hộp thư khác. Đăng nhập đúng hộp thư rồi chạy lại.')
        return data

    async def _click(self,selector,*,timeout_seconds=None):
        if not isinstance(selector,str) or not MARKER_RE.fullmatch(selector):return False
        deadline=time.monotonic()+timeout_seconds if timeout_seconds is not None else None
        if deadline is not None and time.monotonic()>=deadline:return False
        try:
            click_timeout=min(2500,max(1,(deadline-time.monotonic())*1000)) if deadline is not None else 2500
            await self.page.locator(selector).click(timeout=click_timeout)
            settle=min(600,max(0,(deadline-time.monotonic())*1000)) if deadline is not None else 600
            if settle:await self.page.wait_for_timeout(settle)
            return True
        except BrowserError as error:
            if self._host() in AUTH_HOSTS:
                raise OutlookInboxNeedsLogin('Phiên Outlook cần đăng nhập lại trước khi đọc mã Facebook.') from None
            if 'closed' in str(error).casefold():
                raise OutlookInboxError('Tab hộp thư Outlook đã đóng.') from None
            return False

    async def prepare(self,*,require_account=None,timeout_seconds=60):
        """Wait for Outlook, optionally verifying account chrome, up to 60s.

        Recipient mode skips the avatar entirely. Explicitly visible wrong
        accounts still fail, and code extraction always verifies message To.
        """
        if require_account is not None:self.require_account=bool(require_account)
        self.prepared=False
        try:budget=float(timeout_seconds)
        except (TypeError,ValueError):raise OutlookInboxError('Thời gian chờ Outlook không hợp lệ.') from None
        if not math.isfinite(budget):raise OutlookInboxError('Thời gian chờ Outlook không hợp lệ.')
        deadline=time.monotonic()+max(0,min(60,budget))
        if is_outlook_landing(self.page.url):
            raise OutlookInboxNeedsLogin('Cần đăng nhập đúng hộp thư Outlook trước khi đọc mã Facebook.')
        if self._host() not in MAIL_HOSTS|AUTH_HOSTS:
            if self.page.url not in ('about:blank',''):
                raise OutlookInboxError('Tab đọc thư không ở website Outlook hợp lệ.')
            if time.monotonic()>=deadline:
                raise OutlookInboxNeedsLogin('Chưa tải được hộp thư Outlook trong thời gian chờ.')
            try:await self.page.goto(OUTLOOK_URL,wait_until='commit',timeout=max(1,(deadline-time.monotonic())*1000))
            except BrowserError as error:
                if not isinstance(error,BrowserTimeoutError) and not navigation_in_progress(error):
                    raise OutlookInboxError('Không tải được hộp thư Outlook. Kiểm tra kết nối rồi thử lại.') from None
        opened_account=False
        while time.monotonic()<deadline:
            # Outlook can briefly visit login.live.com while restoring a saved
            # Microsoft SSO session. Observe the same tab within this bounded
            # preparation budget; never fill or submit its sign-in form here.
            if self._host() in AUTH_HOSTS:
                data={'state':'loading'}
            else:
                try:data=await self._snapshot()
                except OutlookInboxNeedsLogin:
                    if self._host() not in AUTH_HOSTS:raise
                    data={'state':'loading'}
            if data.get('state')=='mailbox' and (not self.require_account or _address(data.get('account_email'))==self.email):
                if opened_account:
                    try:await self.page.keyboard.press('Escape')
                    except BrowserError:pass
                self.prepared=True
                return
            if self.require_account and data.get('state')=='mailbox' and not opened_account and data.get('account_selector'):
                opened_account=await self._click(data['account_selector'],timeout_seconds=max(0,deadline-time.monotonic()))
            await self._notify('Đang xác nhận đúng hộp thư Outlook.',deadline-time.monotonic())
            await asyncio.sleep(min(1,max(0,deadline-time.monotonic())))
        message='Chưa xác nhận được đúng hộp thư Outlook. Mở menu tài khoản để kiểm tra đăng nhập.' if self.require_account else 'Outlook chưa hiện hộp thư. Kiểm tra tab Outlook rồi Tiếp tục.'
        raise OutlookInboxNeedsLogin(message)

    def _extract(self,data,since):
        if data.get('loading'):return None
        detail=data.get('detail')
        if not isinstance(detail,dict) or detail.get('selection_matches') is False:return None
        fingerprint=_message_fingerprint(detail)
        if fingerprint in self._consumed_messages:return None
        observed=self._first_seen.setdefault(fingerprint,_utc_now())
        requested=_aware_time(since)
        baseline_before=bool(self._baseline_complete and self._baseline_captured_at and requested and self._baseline_captured_at<=requested)
        unseen=fingerprint not in self._baseline_messages and _key_fingerprint(detail) not in self._baseline_unknown_keys
        flags=_safe_diagnostics(detail,self.email,since,allow_minute_rounding=baseline_before and unseen,first_seen_at=observed)
        def score(value):return 10*int(value['sender_verified'])+10*int(value['recipient_verified'])+sum(value.values())
        if score(flags)>=score(self._diagnostics):self._diagnostics=flags
        code=extract_facebook_code(detail,self.email,since,used_code_hashes=self._used_code_hashes,
            allow_minute_rounding=baseline_before and unseen,first_seen_at=observed)
        if code:
            self._used_code_hashes.add(_code_hash(code))
            self._consumed_messages.add(fingerprint)
        return code

    async def capture_baseline(self,*,timeout_seconds=None):
        """Snapshot existing visible mail BEFORE requesting a Facebook code.

        Return the UTC completion time, or None if any folder/tab was not ready.
        An incomplete/late snapshot never permits minute-rounding tolerance.
        Only message fingerprints are retained, never codes, text or URLs.
        """
        deadline=time.monotonic()+max(0,float(timeout_seconds)) if timeout_seconds is not None else None
        if not self.prepared:
            if deadline is None:await self.prepare()
            else:await self.prepare(timeout_seconds=min(60,max(0,deadline-time.monotonic())))
        fingerprints,unknown_keys=set(),set()
        complete=True
        for kind in ('inbox','junk'):
            if deadline is not None and time.monotonic()>=deadline:
                complete=False
                break
            data=await self._snapshot()
            folder=next((row for row in data.get('folders',[]) if row.get('kind')==kind),None)
            if folder is None:
                complete=False
                continue
            if not folder.get('selected'):
                remaining=max(0,deadline-time.monotonic()) if deadline is not None else None
                if not await self._click(folder.get('selector'),timeout_seconds=remaining):
                    complete=False
                    continue
                data=await self._snapshot()
            tab_kinds=('focused','other') if kind=='inbox' and data.get('tabs') else (None,)
            for tab_kind in tab_kinds:
                if deadline is not None and time.monotonic()>=deadline:
                    complete=False
                    break
                current=await self._snapshot()
                if tab_kind is not None:
                    tab=next((row for row in current.get('tabs',[]) if row.get('kind')==tab_kind),None)
                    if tab is None:
                        complete=False
                        continue
                    if not tab.get('selected'):
                        remaining=max(0,deadline-time.monotonic()) if deadline is not None else None
                        if not await self._click(tab.get('selector'),timeout_seconds=remaining):
                            complete=False
                            continue
                        current=await self._snapshot()
                if current.get('state')!='mailbox' or current.get('loading'):
                    complete=False
                    continue
                for item in (current.get('items') or [])[:25]:
                    fingerprints.add(_message_fingerprint(item))
                    if not item.get('subject'):unknown_keys.add(_key_fingerprint(item))
                detail=current.get('detail')
                if isinstance(detail,dict) and detail.get('key'):
                    fingerprints.add(_message_fingerprint(detail))
        self._baseline_messages=fingerprints
        self._baseline_unknown_keys=unknown_keys
        self._baseline_complete=complete
        self._baseline_captured_at=_utc_now()
        self._first_seen.clear()
        return self._baseline_captured_at if complete else None

    async def try_capture_baseline(self,timeout_seconds=10):
        """Recipient preflight: at most 10s, no avatar and no sign-in actions.

        Missing/login/loading UI returns None so Facebook can proceed. An
        explicitly visible wrong mailbox remains an immediate typed failure.
        """
        try:budget=float(timeout_seconds)
        except (TypeError,ValueError):raise OutlookInboxError('Thời gian chờ Outlook không hợp lệ.') from None
        if not math.isfinite(budget):raise OutlookInboxError('Thời gian chờ Outlook không hợp lệ.')
        deadline=time.monotonic()+max(0,min(10,budget))
        self.require_account=False
        try:
            await self.prepare(require_account=False,timeout_seconds=max(0,deadline-time.monotonic()))
            return await self.capture_baseline(timeout_seconds=max(0,deadline-time.monotonic()))
        except OutlookInboxWrongAccount:raise
        except (OutlookInboxError,TimeoutError):
            self._baseline_complete=False
            return None

    async def _read_current(self,since,deadline):
        data=await self._snapshot()
        if data.get('loading'):return None
        code=self._extract(data,since)
        if code:return code
        for item in (data.get('items') or [])[:25]:
            if time.monotonic()>=deadline:return None
            hint=str(item.get('hint') or '')[:12000]
            if not re.search(r'facebook',hint,re.I) or IRRELEVANT_SUBJECT.search(str(item.get('subject') or '')):
                continue
            if not await self._click(item.get('selector')):continue
            opened=await self._snapshot()
            detail=opened.get('detail') or {}
            if detail.get('expand_selector') and not (detail.get('sender') and detail.get('recipients') and detail.get('received_at')):
                if await self._click(detail['expand_selector']):opened=await self._snapshot()
            code=self._extract(opened,since)
            if code:return code
        return None

    async def _scan(self,since,deadline):
        # Folder/tab selections are UI actions only. Never follow email links or
        # visit hidden mailbox endpoints. Refresh/retry is the next poll cycle.
        for kind in ('inbox','junk'):
            if time.monotonic()>=deadline:return None
            data=await self._snapshot()
            folder=next((row for row in data.get('folders',[]) if row.get('kind')==kind),None)
            if folder is not None and not folder.get('selected'):
                if not await self._click(folder.get('selector')):continue
                data=await self._snapshot()
            elif folder is None and data.get('folder')!=kind:
                continue
            tabs=[row for row in data.get('tabs',[]) if row.get('kind') in ('focused','other')] if kind=='inbox' else []
            if tabs:
                for tab_kind in ('focused','other'):
                    current=await self._snapshot()
                    tab=next((row for row in current.get('tabs',[]) if row.get('kind')==tab_kind),None)
                    if tab is None:continue
                    if not tab.get('selected') and not await self._click(tab.get('selector')):continue
                    code=await self._read_current(since,deadline)
                    if code:return code
            else:
                code=await self._read_current(since,deadline)
                if code:return code
        return None

    def _manual_code(self,manual_queue):
        if manual_queue is None:return None
        try:value=manual_queue.get_nowait()
        except asyncio.QueueEmpty:return None
        value=value.get_secret_value() if hasattr(value,'get_secret_value') else value
        code=str(value).strip()
        if not re.fullmatch(r'[0-9]{6,8}',code) or _code_hash(code) in self._used_code_hashes:return None
        self._used_code_hashes.add(_code_hash(code))
        return code

    async def wait_for_code(self,since,timeout_seconds=300,manual_queue=None):
        if _aware_time(since) is None:
            raise OutlookInboxError('Mốc chờ mã phải có múi giờ hợp lệ.')
        try:budget=float(timeout_seconds)
        except (TypeError,ValueError):raise OutlookInboxError('Thời gian chờ mã không hợp lệ.') from None
        if not math.isfinite(budget):raise OutlookInboxError('Thời gian chờ mã không hợp lệ.')
        budget=max(0,min(300,budget))
        deadline=time.monotonic()+budget
        manual=self._manual_code(manual_queue)
        if manual:return manual
        if not self.prepared:await self.prepare(timeout_seconds=min(60,max(0,deadline-time.monotonic())))
        while time.monotonic()<deadline:
            manual=self._manual_code(manual_queue)
            if manual:return manual
            await self._notify('Đang chờ thư mã Facebook mới trong Inbox, Other và Junk. Không tự gửi lại mã.',deadline-time.monotonic())
            try:
                code=await self._scan(since,deadline)
                if code:return code
            except OutlookInboxNeedsLogin:raise
            except OutlookInboxError:
                await self._notify('Outlook chưa tải xong; đang tiếp tục chờ trong thời gian còn lại.',deadline-time.monotonic())
            await asyncio.sleep(min(POLL_SECONDS,max(0,deadline-time.monotonic())))
        raise TimeoutError('Hết thời gian chờ mã Facebook mới. Không tự yêu cầu gửi lại mã.')
