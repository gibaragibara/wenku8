# Wenku8 Lanzou EPUB Downloader

一个独立于原 `wenku8` 项目的蓝奏下载器。

当前仓库的 `main.py` 已经默认集成同一套下载逻辑；这个目录仍然保留，便于单独调试、手动补抓或独立部署。

用途：

- 读取 `merged.csv` 中的 `dl_label` / `dl_pwd`
- 读取 `dl.txt` 第一行中的蓝奏前缀
- 自动打开蓝奏分享页，优先下载简体“合集.zip/.7z/.rar”
- 如果没有合集，则回退下载非 `zht_` 的单卷 `.epub`
- 自动解压压缩包并提取其中的 `.epub` 文件到本地目录

这个项目不依赖原仓库的抓取逻辑，只把原仓库已经产出的蓝奏下载信息当作输入。

设计目标：

- 只处理蓝奏源，不处理 TXT 源
- 首次部署时不回补历史库存，只处理部署后的新条目
- 优先下载简体合集；没有合集时回退到简体单卷 EPUB
- 自动过滤 `zht_` 和“繁体”命名的文件

## 输入文件

需要两个文件：

1. `merged.csv`
2. `dl.txt`

通常来自原 `wenku8` 项目的 `out/` 目录，例如：

```text
/opt/wenku8/out/merged.csv
/opt/wenku8/out/dl.txt
```

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
python downloader.py \
  --merged-csv /opt/wenku8/out/merged.csv \
  --dl-txt /opt/wenku8/out/dl.txt \
  --output-dir /opt/wenku8-lanzou
```

程序会：

- 先从蓝奏分享页读取文件列表
- 优先选择简体 `合集.zip/.7z/.rar`
- 若没有合集，则选择非 `zht_` 的单卷 `.epub`
- 下载归档后自动提取 `.epub`
- 只把非 `zht_` 的 `.epub` 复制到输出目录

默认输出结构：

```text
/opt/wenku8-lanzou/
  archives/
  epubs/
  state.json
```

其中：

- `archives/` 保存下载到的蓝奏合集压缩包
- `epubs/` 保存自动提取出的 `.epub`
- `state.json` 记录基线和已处理的 `dl_label`

## 首次部署行为

首次运行时，程序默认**不会下载当前已有条目**，而是：

- 扫描当前 `merged.csv` 里的所有 `dl_label`
- 写入 `state.json` 作为初始基线
- 后续只下载部署之后新增的蓝奏条目

这意味着：

- 第一次运行不会把当前已有的上千条历史蓝奏记录全部下载一遍
- 只有部署完成后，后续新进入 `merged.csv` 的条目才会被自动处理

对于已经在基线里的旧 `dl_label`，如果后续 `dl_update`、卷号或备注发生变化，
下载器也会把它视为“条目已更新”并重新下载，不会永久跳过。

如果你明确要把当前已有条目也处理掉，可以加：

```bash
--include-existing
```

常见用法：

- 正式部署：不要加 `--include-existing`
- 首次手动验收：加 `--include-existing --limit 1 --name-contains 某书名`

## 常用参数

只处理前 5 条：

```bash
python downloader.py \
  --merged-csv /opt/wenku8/out/merged.csv \
  --dl-txt /opt/wenku8/out/dl.txt \
  --output-dir /opt/wenku8-lanzou \
  --limit 5
```

只下载标题里包含某关键词的条目：

```bash
python downloader.py \
  --merged-csv /opt/wenku8/out/merged.csv \
  --dl-txt /opt/wenku8/out/dl.txt \
  --output-dir /opt/wenku8-lanzou \
  --name-contains 火影
```

强制重新下载已处理过的条目：

```bash
python downloader.py \
  --merged-csv /opt/wenku8/out/merged.csv \
  --dl-txt /opt/wenku8/out/dl.txt \
  --output-dir /opt/wenku8-lanzou \
  --force
```

首次部署后，手动测试当前已有条目：

```bash
python downloader.py \
  --merged-csv /opt/wenku8/out/merged.csv \
  --dl-txt /opt/wenku8/out/dl.txt \
  --output-dir /opt/wenku8-lanzou \
  --include-existing \
  --limit 1
```

验证某一条已存在的蓝奏记录：

```bash
python downloader.py \
  --merged-csv /opt/wenku8/out/merged.csv \
  --dl-txt /opt/wenku8/out/dl.txt \
  --output-dir /opt/wenku8-lanzou \
  --include-existing \
  --limit 1 \
  --name-contains 奇妙故事集
```

显示浏览器界面，便于排障：

```bash
python downloader.py \
  --merged-csv /opt/wenku8/out/merged.csv \
  --dl-txt /opt/wenku8/out/dl.txt \
  --output-dir /opt/wenku8-lanzou \
  --show-browser
```

## Docker

先构建镜像：

```bash
docker build -t wenku8-lanzou-downloader .
```

运行：

```bash
docker run --rm \
  -v /opt/wenku8/out:/input:ro \
  -v /opt/wenku8-lanzou:/data \
  wenku8-lanzou-downloader \
  python downloader.py \
    --merged-csv /input/merged.csv \
    --dl-txt /input/dl.txt \
    --output-dir /data
```

也可以参考 `docker-compose.yml.example`。

## VPS 部署

推荐把这个独立项目部署为一个单独目录，例如：

```text
/opt/lanzou_epub_downloader
```

输入仍然来自原 `wenku8` 项目的输出：

```text
/opt/wenku8/out/merged.csv
/opt/wenku8/out/dl.txt
```

典型部署步骤：

```bash
cd /opt
git clone https://github.com/gibaragibara/wenku8.git
cd wenku8/lanzou_epub_downloader

docker build -t lanzou-epub-downloader:latest .

docker run --rm \
  -v /opt/wenku8/out:/input:ro \
  -v /opt/wenku8-lanzou:/data \
  lanzou-epub-downloader:latest \
  python downloader.py \
    --merged-csv /input/merged.csv \
    --dl-txt /input/dl.txt \
    --output-dir /data
```

如果只是想先验证一条历史记录：

```bash
docker run --rm \
  -v /opt/wenku8/out:/input:ro \
  -v /opt/wenku8-lanzou-test:/data \
  lanzou-epub-downloader:latest \
  python downloader.py \
    --merged-csv /input/merged.csv \
    --dl-txt /input/dl.txt \
    --output-dir /data \
    --include-existing \
    --limit 1 \
    --name-contains 奇妙故事集
```

这个项目默认是单次执行，不是常驻调度器。若要定时运行，建议由 VPS 上的 `cron`、`systemd timer` 或外部任务调度器触发。

## OneDrive 自动上传与清理

仓库还提供了一个可常驻运行的后台守护进程：

```bash
python -m lanzou_epub_downloader.onedrive_sync
```

它会：

- 读取 `out/downloads/state.json` 与 `out/merged.csv`
- 按小说主标题整理本地 EPUB，上传到 `ONEDRIVE_REMOTE_TARGET`
- 确认远端文件大小正确后，自动删除本地 EPUB 与归档文件
- 可选清理 `ONEDRIVE_REMOTE_ROOT` 下历史误传的平铺 EPUB

常用环境变量：

- `ENABLE_ONEDRIVE_UPLOAD=true`
- `ONEDRIVE_REMOTE_TARGET=wenku8_od:轻小说/wenku8`
- `ONEDRIVE_REMOTE_ROOT=wenku8_od:轻小说`
- `ONEDRIVE_UPLOAD_INTERVAL_SECONDS=120`
- `ONEDRIVE_CLEAN_REMOTE_ROOT_DUPLICATES=true`

这个守护进程已经集成到主项目的 `run_scheduler.sh` 中；在 Docker 模式下，只要挂载好 `rclone.conf` 并开启 `ENABLE_ONEDRIVE_UPLOAD=true`，容器会自动后台启动它。

## 说明

- 项目默认优先下载简体蓝奏“合集”压缩包，再提取其中的 `.epub`
- 如果没有合集，则会回退到下载单卷 `.epub`
- 会自动过滤 `zht_` 前缀或“繁体”命名的 EPUB，不会保存到 `epubs/`
- 首次部署只建立基线，不会回补部署前已有的历史条目
- 最终文件下载使用 Playwright 的 `APIRequestContext`，在 VPS 上比直接 `requests` 更稳定
- `zip` 直接用 Python 标准库解压
- `7z` / `rar` 依赖容器内安装的 `7z`
- 如果压缩包里没有 `.epub`，会保留压缩包并在日志中提示
