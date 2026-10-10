
# 目录名 skill-runner 含连字符（部署/镜像命名沿用），Python 包名经本地 junction
# skill_runner -> skill-runner 映射；模块名校验在此豁免。
# pylint: disable=invalid-name,huawei-invalid-name
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""skill_runner —— Playground 在线试用的会话编排服务。

负责 skill 试用会话的生命周期与流式输出，本身不执行 skill 逻辑——执行委托给
可插拔的沙箱执行器（本地 subprocess / 云上 Worker Pod）。
"""

__version__ = "0.0.1"
