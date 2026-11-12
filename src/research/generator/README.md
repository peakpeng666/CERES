# 装箱生成执行器

`runner.py`保存固定提示、候选文本、调用终态和可用token记录。`frozen_inputs.json`包含已有60个装箱生成输入：EOH_TC和IID_TC各30个，原位置73–132。

以下命令从CERES根目录进入本组件后执行；此处列出命令不表示本轮已执行。

```sh
cd src/research/generator
python3 runner.py inspect
python3 -m unittest test_runner
```

`status --ledger <目录>`读取账本；`run-one`使用明确的账本、位置和OpenCode二进制路径执行单槽。已创建槽位不会自动再次发出请求；未知终态保留并阻止后续派发。记录含提示、配置、回答与退出状态。

固定配置为GLM-5.3、high effort、64,000输出token上限、900秒、temperature 0.7、top_p 0.9；OpenCode二进制哈希由执行器检查。来源见[input_provenance.json](input_provenance.json)。

当前入口绑定已有实验输入。P5需接入新的任务配置、统一预算和候选反馈；新实验使用新的运行ID与独立输入。见[下一阶段](../../../docs/下一阶段.md)。
