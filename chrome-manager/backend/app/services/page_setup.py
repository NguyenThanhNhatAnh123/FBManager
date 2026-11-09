"""Conservative Page setup through visible Facebook forms and saved readback."""
import asyncio
import hashlib
import inspect
import json
import re
from urllib.parse import urlsplit

from app.core.page_recipient import PageSetup,asset_path
from app.services.facebook_identity import switch_to_page_identity


class PageSetupNeedsUser(ValueError):
    """The current rendered layout cannot be edited or verified safely."""


LABELS={
    'bio':r'^(?:bio|biography|describe (?:yourself|your page)|tiểu sử|giới thiệu ngắn|biographie)$',
    'street':r'^(?:street(?: address)?|address|địa chỉ(?: đường phố)?|rue|adresse)$',
    'city':r'^(?:city(?:/town)?|town(?:/city)?|thành phố(?:/thị xã)?|ville)$',
    'postal_code':r'^(?:zip(?: code)?|postal code|zip/postal code|mã bưu chính|code postal)$',
    'country':r'^(?:country(?:/region)?|quốc gia(?:/khu vực)?|pays)$',
    'categories':r'^(?:categor(?:y|ies)|danh mục|hạng mục|catégories?)$',
}
OPEN={
    'avatar':r'^(?:edit (?:profile picture|avatar)|add (?:profile picture|avatar)|profile picture action menu|chỉnh sửa ảnh đại diện|thêm ảnh đại diện|modifier la photo de profil|ajouter une photo de profil)$',
    'cover':r'^(?:add cover photo|edit cover photo|edit cover|thêm ảnh bìa|chỉnh sửa ảnh bìa|modifier la photo de couverture|ajouter une photo de couverture)$',
    'bio':r'^(?:edit bio|add (?:a )?bio|edit biography|chỉnh sửa tiểu sử|thêm tiểu sử|modifier la biographie|ajouter une biographie)$',
    'address':r'^(?:edit address|add (?:an )?address|chỉnh sửa địa chỉ|thêm địa chỉ|modifier l’adresse|ajouter une adresse)$',
    'categories':r'^(?:edit categor(?:y|ies)|add (?:a )?categor(?:y|ies)|chỉnh sửa danh mục|thêm danh mục|modifier les catégories|ajouter une catégorie)$',
}
SAVE=r'^(?:save|save changes|lưu|lưu thay đổi|enregistrer|enregistrer les modifications)$'
CLOSE=r'^(?:close|đóng|fermer)$'
UPLOAD=r'^(?:upload photo|upload picture|upload a photo|tải ảnh lên|tải lên ảnh|importer une photo|téléverser une photo)$'
MESSAGES={
    'avatar':'Chưa xác minh được ảnh đại diện đã lưu. Kiểm tra hộp sửa ảnh trong Chrome rồi tiếp tục.',
    'cover':'Chưa xác minh được ảnh bìa đã lưu. Kiểm tra hộp sửa ảnh trong Chrome rồi tiếp tục.',
    'bio':'Giao diện tiểu sử chưa hỗ trợ hoặc chưa đọc lại được nội dung đã lưu. Kiểm tra Chrome rồi tiếp tục.',
    'address':'Giao diện địa chỉ chưa hỗ trợ hoặc chưa đọc lại được từng mục đã lưu. Kiểm tra Chrome rồi tiếp tục.',
    'categories':'Chưa chọn hoặc đọc lại được đúng danh mục Facebook. Kiểm tra Chrome rồi tiếp tục.',
}

# Read rendered header media only. No requests, cookies, application stores or API.
MEDIA_READ=r'''({kind})=>{
 const visible=e=>!!(e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
 const items=[];
 for(const e of document.querySelectorAll('img,svg image')){
  if(!visible(e)||e.closest('[role="article"],[role="dialog"],[data-pagelet^="FeedUnit_"]'))continue;
  const r=e.getBoundingClientRect();if(r.width<64||r.height<64)continue;
  const labels=[e.alt,e.getAttribute('aria-label'),e.getAttribute('data-imgperflogname'),e.closest('[role="img"],svg')?.getAttribute('aria-label')].filter(Boolean).join(' ');
  const appropriate=kind==='avatar'?/profile (?:picture|photo)|profilePic|ảnh đại diện|photo de profil/i.test(labels):/cover(?:photo)?|ảnh bìa|couverture/i.test(labels);
  if(!appropriate)continue;
  const src=e.currentSrc||e.src||e.getAttribute('href')||e.getAttribute('xlink:href')||'';
  const loaded=e.tagName.toLowerCase()!=='img'||e.complete&&e.naturalWidth>0;
  if(src&&loaded)items.push({src,area:r.width*r.height});
 }
 items.sort((a,b)=>b.area-a.area);return items[0]?.src||'';
}'''


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _input_digest(key,value,root):
    # Same input fingerprint as PageRecipientManager: replacing an asset in
    # place must invalidate a completed photo, even when its filename is stable.
    if value and key in ('avatar','cover'):
        path=asset_path(value,root)
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        return _digest({'path':str(path),'sha256':digest.hexdigest()})
    return _digest(value)


def _clean(value):return ' '.join(str(value or '').split()).casefold()


def _media_key(value):
    parsed=urlsplit(value or '')
    return (parsed.hostname or '',parsed.path) if parsed.scheme in ('http','https') else ('',value or '')


class PageSetupController:
    def __init__(self,page,page_url,page_name,asset_root,field_callback):
        self.page,self.page_url,self.page_name=page,page_url,page_name
        self.asset_root,self.field_callback=asset_root,field_callback

    async def _notify(self,key,status,message='',**extra):
        result=self.field_callback(key,status,message,**extra)
        if inspect.isawaitable(result):await result

    async def _guard(self):
        await switch_to_page_identity(self.page,self.page_url,self.page_name)

    async def _scope(self):
        for dialog in reversed(await self.page.get_by_role('dialog').all()):
            if await dialog.is_visible():return dialog
        return self.page

    async def _find(self,role,pattern,*,scope=None,required=True):
        scope=scope or await self._scope()
        matches=[]
        for item in await scope.get_by_role(role,name=re.compile(pattern,re.I)).all():
            if await item.is_visible() and await item.is_enabled():matches.append(item)
        if len(matches)==1:return matches[0]
        if matches or required:raise PageSetupNeedsUser('Giao diện có nhiều mục trùng tên hoặc thiếu mục cần sửa. Kiểm tra Chrome rồi tiếp tục.')
        return None

    async def _click(self,button):
        await self._guard()
        await button.click(timeout=7000)
        await self.page.wait_for_timeout(400)

    async def _textbox(self,key,required=True):
        scope=await self._scope()
        for role in ('textbox','combobox'):
            field=await self._find(role,LABELS[key],scope=scope,required=False)
            if field is not None:return field
        if required:raise PageSetupNeedsUser('Chưa tìm thấy ô nhập có nhãn rõ ràng cho mục Page.')
        return None

    async def _section_button(self,key):
        """Recognize an exact section heading and its one local Add/Edit action."""
        scope=await self._scope()
        pattern=LABELS['street' if key=='address' else key]
        sections=await scope.get_by_text(re.compile(pattern,re.I),exact=True).all()
        matches=[]
        for heading in sections:
            if not await heading.is_visible():continue
            container=heading.locator('xpath=..')
            for _ in range(3):
                actions=[]
                for button in await container.get_by_role('button',name=re.compile(r'^(?:edit|add|chỉnh sửa|thêm|modifier|ajouter)$',re.I)).all():
                    if await button.is_visible() and await button.is_enabled():actions.append(button)
                if len(actions)==1:matches.append(actions[0]);break
                if actions:break
                container=container.locator('xpath=..')
        if len(matches)==1:return matches[0]
        return None

    async def _open_editor(self,key):
        direct=await self._find('button',OPEN[key],required=False)
        if direct is not None:await self._click(direct);return
        # Page header Edit opens Facebook's own Page editor. Never use Settings,
        # Page access, invitations or generic unlabeled edit icons as fallbacks.
        edit=await self._find('button',r'^(?:edit|edit page|edit profile|chỉnh sửa|chỉnh sửa trang|modifier|modifier la page)$',required=False)
        if edit is not None:await self._click(edit)
        if key=='bio' and await self._textbox('bio',False) is not None:return
        if key=='address' and await self._textbox('street',False) is not None:return
        if key=='categories' and await self._textbox('categories',False) is not None:return
        local=await self._section_button(key)
        if local is None:raise PageSetupNeedsUser(MESSAGES[key])
        await self._click(local)

    async def _fill(self,element,value,*,suggestion=False):
        await self._guard()
        await element.fill(value,timeout=7000)
        if suggestion:
            await self.page.wait_for_timeout(500)
            option=await self._find('option',r'^'+re.escape(value)+r'$',required=False)
            if option is None:raise PageSetupNeedsUser('Facebook yêu cầu chọn đúng gợi ý đang hiển thị. Chọn thủ công rồi tiếp tục.')
            await self._click(option)

    async def _save_reload(self):
        save=await self._find('button',SAVE)
        await self._click(save)
        # Reload removes editor-local input state. Only persisted readback after
        # this navigation can complete a field.
        for _ in range(20):
            dialogs=[item for item in await self.page.get_by_role('dialog').all() if await item.is_visible()]
            if not dialogs:break
            await self.page.wait_for_timeout(500)
        else:raise PageSetupNeedsUser('Hộp chỉnh sửa chưa đóng sau khi lưu. Kiểm tra lỗi hiển thị trong Chrome.')
        await self.page.reload(wait_until='domcontentloaded',timeout=45000)
        await self.page.wait_for_timeout(700)
        await self._guard()

    async def _dismiss_readback(self):
        scope=await self._scope()
        if scope is self.page:return
        button=await self._find('button',CLOSE,scope=scope,required=False)
        if button is not None:await button.click(timeout=5000)
        else:await self.page.keyboard.press('Escape')

    async def _bio(self,value):
        await self._open_editor('bio')
        field=await self._textbox('bio')
        await self._fill(field,value)
        await self._save_reload()
        await self._open_editor('bio')
        field=await self._textbox('bio')
        if _clean(await field.input_value())!=_clean(value):raise PageSetupNeedsUser(MESSAGES['bio'])
        await self._dismiss_readback()

    async def _address(self,value):
        values={key:item.strip() for key,item in value.items() if item and item.strip()}
        await self._open_editor('address')
        for key,item in values.items():
            field=await self._textbox(key)
            suggestion=await field.get_attribute('role')=='combobox' or await field.get_attribute('aria-autocomplete') in ('list','both')
            await self._fill(field,item,suggestion=suggestion)
        await self._save_reload()
        await self._open_editor('address')
        for key,item in values.items():
            field=await self._textbox(key)
            if _clean(await field.input_value())!=_clean(item):raise PageSetupNeedsUser(MESSAGES['address'])
        await self._dismiss_readback()

    async def _category_values(self):
        scope=await self._scope()
        values=[]
        # Selected categories are visible chips with Facebook's Remove action.
        for button in await scope.get_by_role('button',name=re.compile(r'^(?:remove|supprimer|xóa|gỡ)\s+.+',re.I)).all():
            if not await button.is_visible():continue
            label=await button.get_attribute('aria-label') or await button.inner_text()
            values.append(re.sub(r'^(?:remove|supprimer|xóa|gỡ)\s+','',label,flags=re.I).strip())
        field=await self._textbox('categories',False)
        if field is not None:
            value=await field.input_value()
            if value.strip():values.append(value.strip())
        return {_clean(value) for value in values}

    async def _categories(self,values):
        await self._open_editor('categories')
        current=await self._category_values()
        for value in values:
            if _clean(value) in current:continue
            field=await self._textbox('categories')
            await self._fill(field,value,suggestion=True)
            current=await self._category_values()
            if _clean(value) not in current:raise PageSetupNeedsUser(MESSAGES['categories'])
        await self._save_reload()
        await self._open_editor('categories')
        if not {_clean(value) for value in values}.issubset(await self._category_values()):raise PageSetupNeedsUser(MESSAGES['categories'])
        await self._dismiss_readback()

    async def _photo(self,key,value):
        path=asset_path(value,self.asset_root)
        before=await self.page.evaluate(MEDIA_READ,{'kind':key})
        button=await self._find('button',OPEN[key])
        await self._click(button)
        scope=await self._scope()
        inputs=scope.locator('input[type="file"]')
        if await inputs.count()==1:
            await self._guard()
            await inputs.first.set_input_files(str(path),timeout=7000)
        else:
            upload=await self._find('button',UPLOAD)
            await self._guard()
            async with self.page.expect_file_chooser(timeout=7000) as pending:
                await upload.click(timeout=7000)
            chooser=await pending.value
            await self._guard()
            await chooser.set_files(str(path),timeout=7000)
        await self.page.wait_for_timeout(700)
        await self._save_reload()
        after=await self.page.evaluate(MEDIA_READ,{'kind':key})
        if not after or _media_key(before)==_media_key(after):raise PageSetupNeedsUser(MESSAGES[key])

    async def apply(self,setup,existing_fields=None):
        """Apply nonblank inputs; a saved field needs persisted visible readback.

        Existing completed fields with an unchanged input fingerprint are left
        alone. Categories are added, preserving categories already on the Page.
        """
        setup=setup if isinstance(setup,PageSetup) else PageSetup.model_validate(setup)
        requested=setup.requested();existing_fields=existing_fields or {};results={}
        for key in ('avatar','cover','bio','address','categories'):
            value=requested.get({'avatar':'avatar_path','cover':'cover_path'}.get(key,key))
            try:fingerprint=_input_digest(key,value,self.asset_root)
            except Exception:
                await self._notify(key,'needs_user',MESSAGES[key],fingerprint=_digest(value))
                raise PageSetupNeedsUser(MESSAGES[key]) from None
            if not value:
                await self._notify(key,'skipped','Chưa có dữ liệu; giữ nguyên mục này.',fingerprint=fingerprint)
                results[key]='skipped';continue
            previous=existing_fields.get(key,{})
            if previous.get('status')=='completed' and previous.get('fingerprint')==fingerprint:
                results[key]='completed';continue
            try:
                await self._guard()
                await self._notify(key,'updating','Đang cập nhật và đọc lại mục Page.',fingerprint=fingerprint)
                if key in ('avatar','cover'):await self._photo(key,value)
                elif key=='bio':await self._bio(value)
                elif key=='address':await self._address(value)
                else:await self._categories(value)
            except asyncio.CancelledError:raise
            except Exception:
                await self._notify(key,'needs_user',MESSAGES[key],fingerprint=fingerprint)
                raise PageSetupNeedsUser(MESSAGES[key]) from None
            await self._notify(key,'completed','Đã lưu và xác minh lại từ giao diện Facebook.',fingerprint=fingerprint)
            results[key]='completed'
        return results
