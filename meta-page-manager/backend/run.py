"""Independent local launcher. All data and configuration belong to this root."""
import os
from pathlib import Path
import threading
import webbrowser
from filelock import FileLock,Timeout
import uvicorn
from page_manager.app import create_app


def main():
    root=Path(__file__).resolve().parents[1]
    try:
        from dotenv import load_dotenv
        load_dotenv(root/'.env',override=False)
    except ImportError:pass
    port=int(os.environ.get('MPM_PORT','8010'))
    if not 1024<=port<=65535 or port==8000:raise ValueError('MPM_PORT không hợp lệ hoặc trùng Chrome Manager.')
    data=root/'data'
    if data.is_symlink() or data.exists() and data.resolve()!=data:
        raise SystemExit('Thư mục dữ liệu Page Manager phải nằm riêng trong ứng dụng này.')
    data.mkdir(exist_ok=True)
    lock_path=data/'page-manager.lock'
    if lock_path.is_symlink() or lock_path.exists() and lock_path.stat().st_nlink>1:
        raise SystemExit('File khóa dữ liệu Page Manager không hợp lệ.')
    lock=FileLock(str(lock_path))
    try:lock.acquire(timeout=0)
    except Timeout:raise SystemExit('Page Manager đang chạy trên dữ liệu riêng này.') from None
    try:
        app=create_app(root,port=port,graph_version=os.environ.get('MPM_GRAPH_VERSION','v26.0'))
        if os.environ.get('MPM_OPEN_BROWSER','true').lower() in ('true','1','yes'):
            timer=threading.Timer(1.5,lambda:webbrowser.open(f'http://127.0.0.1:{port}/'));timer.daemon=True;timer.start()
        uvicorn.run(app,host='127.0.0.1',port=port,access_log=False,log_level='warning')
    finally:lock.release()


if __name__=='__main__':main()
