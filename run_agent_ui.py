"""Launch the local evidence UI and explicit new-paper agent review service."""
import argparse
import sys
from web_backend.server import serve


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--desktop-stdio', action='store_true',
                        help='通过桌面客户端专用标准输入通道执行安全退出握手。')
    options = parser.parse_args()
    if not 1024 <= options.port <= 65535: parser.error('端口必须为1024～65535。')
    serve(options.port, desktop_stdio=options.desktop_stdio)
