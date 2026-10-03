import argparse
import os

import uvicorn

from shelf_finder import config
from shelf_finder.app import create_app
from shelf_finder.library import Library


def main() -> None:
    parser = argparse.ArgumentParser(prog="shelf-finder", description="找书：直接运行即启动 Web 服务")
    sub = parser.add_subparsers(dest="command")
    export = sub.add_parser("export", help="把全部照片和书脊导出成静态网站（只查看，不含上传、识别和编辑）")
    export.add_argument("out_dir", help="输出目录，例如 site")
    args = parser.parse_args()

    library = Library(config.DATA_DIR)
    if args.command == "export":
        from shelf_finder.export import export_site

        export_site(library, args.out_dir)
        print(f"已导出到 {args.out_dir}")
        return

    # 绑定全部网卡，手机在同一局域网里用浏览器访问
    uvicorn.run(
        create_app(library, recognition=config.RECOGNITION),
        host=os.environ.get("SHELF_HOST", "0.0.0.0"),
        port=int(os.environ.get("SHELF_PORT", "8000")),
    )
