import argparse
import json

from .generator import SCENARIOS
from .service import run_pipeline, seed_demo


def main():
    parser = argparse.ArgumentParser(description="Nhóm 6 - AI Data Observability")
    cmds = parser.add_subparsers(dest="command", required=True)
    cmds.add_parser("seed", help="Nạp dữ liệu minh họa (không xóa dữ liệu cũ)")
    cmds.add_parser("reset", help="Xóa dữ liệu demo cũ và tạo mới")
    run = cmds.add_parser("run", help="Chạy một pipeline mô phỏng")
    run.add_argument("--scenario", choices=list(SCENARIOS), default="normal")
    args = parser.parse_args()
    if args.command == "seed":
        result = seed_demo()
    elif args.command == "reset":
        result = seed_demo(reset=True)
    else:
        result = run_pipeline(args.scenario)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
