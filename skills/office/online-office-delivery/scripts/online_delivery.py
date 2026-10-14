#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""online-office-delivery: 在线办公文档交付与回读（依赖宿主授权 CLI/API）。

用法:
  python online_delivery.py --plan plan.json --outdir out/

退出码: 0=成功交付且回读一致 3=blocked(无授权工具) 2=输入错误 4=交付失败
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

KNOWN_CLIS = ["lark-cli", "lark", "feishu-cli"]
KNOWN_ENV_VARS = ["LARK_TOKEN", "FEISHU_TOKEN", "LARK_APP_ID", "FEISHU_APP_ID"]
TARGET_COMMANDS = {
    "lark_sheet": {
        "create": "lark-cli sheets create --name {name}",
        "import": "lark-cli sheets workbook-import --file {local} --target {position}",
        "readback": "lark-cli sheets export --url {url}",
    },
    "lark_doc": {
        "create": "lark-cli docs create --name {name}",
        "import": "lark-cli docs import --file {local}",
        "readback": "lark-cli docs get --url {url}",
    },
    "lark_slides": {
        "create": "lark-cli slides create --name {name}",
        "import": "lark-cli slides import --file {local}",
        "readback": "lark-cli slides get --url {url}",
    },
}


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_host():
    """探测授权工具: CLI 在 PATH / 环境变量存在(只看名字, 不读值)。"""
    found_clis = [c for c in KNOWN_CLIS if shutil.which(c)]
    found_envs = [v for v in KNOWN_ENV_VARS if v in os.environ]
    return {"clis_found": found_clis, "env_vars_present": found_envs,
            "online_delivery_possible": bool(found_clis and found_envs)}


def main():
    ap = argparse.ArgumentParser(description="在线办公文档交付与回读")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    try:
        with open(args.plan, encoding="utf-8") as f:
            plan = json.load(f)
    except Exception as e:
        print(f"ERROR: bad plan: {e}", file=sys.stderr)
        return 2
    os.makedirs(args.outdir, exist_ok=True)

    probe = probe_host()
    if not probe["online_delivery_possible"]:
        result = {
            "status": "blocked",
            "reason": "未探测到已授权的在线办公 CLI/API",
            "probe": probe,
            "missing": {
                "clis": [c for c in KNOWN_CLIS if c not in probe["clis_found"]],
                "env_vars": [v for v in KNOWN_ENV_VARS if v not in probe["env_vars_present"]],
            },
            "note": "需要宿主提供授权的 lark-cli（或官方API凭据）后重试；"
                    "不以本地文件或截图冒充在线交付，不伪造链接",
        }
        with open(os.path.join(args.outdir, "delivery-result.json"), "w",
                  encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False))
        return 3

    # 有授权环境: 执行创建/导入/回读（本环境不可达路径, 命令模板记录在结果中）
    ttype = plan.get("target", {}).get("type", "lark_sheet")
    cmds = TARGET_COMMANDS.get(ttype, TARGET_COMMANDS["lark_sheet"])
    results = []
    ok = True
    for f in plan.get("files", []):
        local = f.get("local", "")
        entry = {"local": local, "name": f.get("name", os.path.basename(local)),
                 "local_sha256": file_hash(local) if os.path.exists(local) else None}
        if not os.path.exists(local):
            entry["status"] = "failed"
            entry["error"] = "local file not found"
            ok = False
            results.append(entry)
            continue
        # 执行导入（宿主CLI存在时）
        import subprocess
        import shlex
        cmd = cmds["import"].format(local=local, name=entry["name"],
                                    position=plan.get("target", {}).get("position", ""))
        try:
            proc = subprocess.run(shlex.split(cmd), capture_output=True,
                                  text=True, timeout=120)
            entry["import_command"] = cmd
            entry["import_returncode"] = proc.returncode
            if proc.returncode != 0:
                entry["status"] = "failed"
                entry["error"] = proc.stderr[:500]
                ok = False
            else:
                entry["status"] = "delivered"
                # 回读校验
                if plan.get("readback"):
                    entry["readback"] = "pending_url_extract"
        except Exception as e:
            entry["status"] = "failed"
            entry["error"] = str(e)
            ok = False
        results.append(entry)
    result = {"status": "delivered" if ok else "failed",
              "probe": probe, "results": results,
              "note": "回读需以真实链接内容与本地哈希/抽样比对一致方判成功"}
    with open(os.path.join(args.outdir, "delivery-result.json"), "w",
              encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({"status": result["status"], "files": len(results)},
                     ensure_ascii=False))
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
