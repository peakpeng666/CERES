# 安装与离线运行

科研面板读取已有BPP实验，展示结果与轨迹。数据转换核对原始输入并生成静态接口；科学核验重放既有记录，不重新训练或运行历史策略搜索。脚本从自身位置定位项目。

## 运行环境

Python支持3.10–3.13，项目内`.venv`使用`requirements.lock`固定NumPy2.2.6及官方wheel的SHA256。Node/npm依赖由`package-lock.json`固定。已有运行检查与环境版本见[检查记录](../../checks.json)。安装依赖需要网络，构建后的面板使用本地数据、字体和资源。

Ubuntu需预装Python、venv支持和Node/npm；中文字体可安装`fonts-noto-cjk`。Windows可在Ubuntu WSL中执行相同命令。各环境重新创建`.venv`和`node_modules`。WSL挂载盘发生监听问题时，可将项目放在WSL本地目录。

## 从当前项目启动

取得CERES后进入`src/frontend`。Drive下载需先按根目录说明还原四个相对目录链接。

```bash
cd src/frontend
bash scripts/console.sh setup
bash scripts/console.sh data-convert
bash scripts/console.sh validate
bash scripts/console.sh test
bash scripts/console.sh build
bash scripts/console.sh serve
```

预览默认监听`http://127.0.0.1:4173`。开发使用`bash scripts/console.sh dev`，端口以终端输出为准；预览可用`BPP_PORT=4174 bash scripts/console.sh serve`调整。

## 数据与校验

`inputs/`、`research/`和`public/data/`分别链接至CERES的`data/bpp/inputs`、`data/bpp/records`和`data/bpp/exports`。当前项目同时保存原始输入、实验记录和派生展示数据。

原始实验ZIP的SHA256为`5f47d49fade8f926bb5751818b038288a772b711d4249d70a7a392eeb9719350`。`data-convert`先校验整个ZIP、MANIFEST成员、大小和哈希，再导入记录并生成静态接口；原始输入或现有记录字节不符时停止。导入与科学核验记录见[import_report.json](../artifacts/import_report.json)和[validation_report.json](../artifacts/validation_report.json)。

## 浏览器检查

日常使用无需Playwright。自动化浏览器检查的依赖由`tools/browser/`锁文件固定：

```bash
bash scripts/console.sh browser-setup --with-deps
fc-match 'Noto Sans CJK SC'
```

首次安装会下载Chromium和系统依赖。中文截图需实际中文字体。构建与启动预览后，在另一终端运行`bash scripts/browser_acceptance.sh`；结果写入`artifacts/`。2026-10-06的产品验收范围和实际限制见[验收记录](ACCEPTANCE.md)，后续运行检查见[检查记录](../../checks.json)。

## 独立重建与打包

`python3 scripts/clean_rebuild.py --parent /tmp`从源码、锁文件和原始ZIP建立独立目录，依次执行安装、转换、核验、测试与构建。`--plan`查看计划，`--stage-only`只复制文件。实际命令、环境、退出码和日志写入`artifacts/clean_rebuild.json`及`artifacts/clean_rebuild_logs/`。

`bash scripts/console.sh package`生成独立前端ZIP及`.sha256`，包含源码、锁文件、说明、测试、原始输入和已有验收附件；派生数据、构建输出与依赖缓存在使用时重建。CERES完整项目仍保留本节列出的数据目录。
