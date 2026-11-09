export const PAGE_INFORMATION_REQUIRED='Page chưa đổi thông tin. Chỉnh sửa Page trước khi bật đăng.';
export function canPublishPage(page){return page&&Object.hasOwn(page,'can_publish')?page.can_publish===true:page?.information_status==='updated'}
