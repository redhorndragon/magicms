# RootVocab · 按词根记雅思托福词汇

本地运行的 Web 英语词汇工具。**按词根 / 前缀 / 后缀把单词串起来看**，每个词给出英音美音音标、词性、中文释义和双语例句；记不住的一键标记状态，随时回来复习。

单机免登录，数据全部存在本机 SQLite（`data/vocab.db`），不联网也能用。

## 界面速览

| 页面 | 作用 |
| --- | --- |
| `/` 概览 | 掌握度环形进度、词量 Top 词根卡片、快捷入口 |
| `/roots/` 词根总览 | 按「词根 / 前缀 / 后缀」三类分组，每片卡片带含义、来源、词量与掌握进度 |
| `/words` 单词列表 | 左栏词根树 + 右栏单词卡，支持考试类型、掌握状态、词根、关键词筛选与分页 |
| `/word/<id>` 单词详情 | 大字号单词、英美双音标、词性、义项列表、双语例句（目标词高亮）、同词根串记 |
| `/review` 生词本 | 只列「不认识 / 学习中」的词，点卡片翻面看释义例句，标记掌握即移出 |

标记的三种状态：`不认识`（红）、`学习中`（琥珀）、`已掌握`（绿），外加 ⭐ 收藏。所有标记都是异步提交，不刷新页面。

## 环境准备

> ⚠️ 本机系统的 `/usr/bin/python3` 因 Xcode 命令行工具配置异常而不可用（报 `unable to locate xcodebuild`）。项目已使用独立的 Python 3.11.9 建好了 `.venv`，**所有命令请用 `.venv/bin/python`，不要用 `python3`**。

首次搭建（虚拟环境已存在时可跳过）：

```bash
# 用已安装的独立 Python 建虚拟环境
/Users/gengcy/.workbuddy/binaries/python/versions/3.11.9/bin/python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

依赖只有两项：`flask`、`requests`。

## 三步跑起来

```bash
# 1) 下载开源语料到 data/raw（约 60MB，已存在则跳过）
.venv/bin/python scripts/fetch_corpus.py

# 2) 清洗入库：筛出雅思/托福词条 + 拆分词性释义 + 关联例句
.venv/bin/python scripts/build_db.py

# 3) 按词根归类单词
.venv/bin/python scripts/classify_roots.py
```

然后启动：

```bash
.venv/bin/python run.py            # 默认 http://127.0.0.1:5000，会自动打开浏览器
.venv/bin/python run.py --port 5055 --debug
```

随时可用 `.venv/bin/python scripts/check_env.py` 检查解释器、依赖、语料、数据表是否就绪。

## 数据来源

词表来自开源仓库 [zhenghaoyang24/english-vocabulary](https://github.com/zhenghaoyang24/english-vocabulary)：

- `tb_book.json` / `tb_voc_book.json`：单词书与词书↔词关联，据此筛出
  - **雅思词汇念念不忘乱序版**（5382 词）
  - **托福高频词汇精讲**（2760 词）
  - 两书去重后共 **6504 词**
- `tb_vocabulary.json`：单词拼写、英音/美音音标、词性与中文释义混合字段、词频
- `tb_voc_examples.json`：例句，按热度取每词前 4 条 → 库中共 17240 条，覆盖 90% 单词

原数据的 `paraphrase` 是「`vt.& vi.撤回或撤消,缩回,缩进`」这种词性与释义混在一起的形式，`scripts/build_db.py` 会在**首个中文字符处**切开，拆成独立的词性标签与释义列表。

字段完整度：仅 13 词缺音标、3 词缺词性、644 词缺例句（UI 会降级显示占位）。

## 关于「按词根分类」的实现说明

公开数据源没有可用的词根/词缀数据集，因此采用**人工词根种子库 + 规则匹配**的方案：

- `data/roots_seed.json`：259 个词素（词根 / 前缀 / 后缀），每条含含义、语源（拉丁/希腊/古英语）与常见变体
- `scripts/classify_roots.py` 的匹配规则：
  - 前缀型：以变体开头，且剩余主干 ≥ 4 字符（防止 `read` 被 `re-` 误伤）
  - 后缀型：以变体结尾，剩余 ≥ 3 字符
  - 词根型：单词包含变体，剩余 ≥ 3 字符
  - 多命中时按 `词根 > 前缀 > 后缀`、其次匹配片段越长越优先的打分选主词根
- 当前**自动归类覆盖约 48.5%**（3155 / 6504）。未命中的多为日耳曼语源基础词（be、if）与复合词（watercraft、marketer），它们在界面上统一归入「**未归类**」分组，仍然可以浏览、标记和复习。

想纠正归类结果，编辑 `data/root_overrides.json`（键为单词小写拼写，值为词根名，不需要词根就写 `null`），再重跑：

```bash
.venv/bin/python scripts/classify_roots.py
```

被纠正的词会标记为「人工归类」，重跑脚本不会覆盖（除非加 `--force`）。

## 目录结构

```
app/            Flask 应用
  config.py       路径与外部地址配置
  db.py           SQLite 连接封装、建表 SQL
  models/queries.py   全部查询（统一参数化）
  services/progress_service.py   掌握状态业务规则
  routes/         main / roots / api 三个蓝图
  templates/      Jinja2 模板
  static/         CSS 与异步标记 JS
scripts/
  fetch_corpus.py     下载语料（支持缓存与重试）
  build_db.py         清洗并建库（幂等重建）
  classify_roots.py   词根归类与抽样复核
  check_env.py        环境自检
data/
  roots_seed.json     词根种子数据
  root_overrides.json 人工纠正表
  raw/                下载的原始语料（gitignore）
  vocab.db            SQLite 库（gitignore）
```

## 常用命令

```bash
.venv/bin/python scripts/fetch_corpus.py --force              # 重下语料
.venv/bin/python scripts/build_db.py --keep-progress          # 重建词库但保留学习进度
.venv/bin/python scripts/classify_roots.py --audit            # 归类后抽样复核（默认 30 组 × 8 词）
.venv/bin/python run.py --no-browser                          # 不自动开浏览器
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/word/<id>/cycle` | 三态循环切换掌握状态 |
| POST | `/api/word/<id>/status` | 直接设置 `{"status":"learning"}` |
| POST | `/api/word/<id>/star` | 切换收藏 |
| POST | `/api/word/<id>/note` | 保存笔记 |
| GET | `/api/stats?exam=ielts` | 获取统计 |
| POST | `/api/progress/reset` | 清空进度（可传 `{"status":"mastered"}` 定向清除） |
