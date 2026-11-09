"""Local provisioning only. Never writes passwords to stdout or a shared config."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from via_auth.store import IdentityStore, IdentityError, protect_private


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('bootstrap', 'status', 'recover'))
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--username', default='admin')
    args = parser.parse_args()
    store = IdentityStore(args.data_dir)
    if args.command == 'status':
        print(json.dumps({'users': store.list_users()}, ensure_ascii=True))
        return
    if args.command == 'recover':
        store.ensure_service_key()
        user=next((user for user in store.list_users() if user['username'].casefold()==args.username.casefold()),None)
        if user is None:
            raise IdentityError('user_not_found',404)
        # Possession of the restricted local auth directory is required. There
        # is no HTTP password recovery endpoint and no shared default password.
        result=store.update_user(None,user['id'],{'revision':user['revision'],'active':True,'reset_password':True})
        from uuid import uuid4
        output=store.directory/('recovered-login-'+uuid4().hex+'.txt')
        import os
        fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as file:
            file.write(user['username']+'\nMật khẩu tạm: '+result['temporary_password']+'\nĐổi mật khẩu ngay sau khi đăng nhập.\n')
        protect_private(output)
        print(json.dumps({'recovered':user['username'],'credential_file':str(output),'password_change_required':True}))
        return
    output = store.directory / 'initial-users.txt'
    if store.count():
        raise IdentityError('users_already_initialized', 409)
    if output.exists():
        raise IdentityError('credential_file_exists', 409)
    # The private parent ACL is established before any credential file exists.
    store.ensure_service_key()
    users = store.bootstrap()
    import os
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        file.write('Via: thông tin đăng nhập ban đầu — giữ riêng, xóa sau khi cả 3 người đổi mật khẩu.\n')
        file.write('Đăng nhập tại http://127.0.0.1:8000/auth/login sau khi khởi động bản mới.\n\n')
        for user in users:
            file.write(f"{user['username']} ({user['role']})\nMật khẩu tạm: {user['password']}\n\n")
    protect_private(output)
    print(json.dumps({'created': len(users), 'credential_file': str(output), 'password_change_required': True}))


if __name__ == '__main__':
    try:
        main()
    except IdentityError as error:
        print(json.dumps({'error': error.code}), file=sys.stderr)
        raise SystemExit(1)
