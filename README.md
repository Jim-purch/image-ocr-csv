# OCR 批量图片处理工具 (OCR Batch Image Processor)

这是一个基于 Python 和 PySide6 开发的图形化 OCR 处理工具。它能够自动监测文件夹中的图片，调用 Umi-OCR 接口进行识别，并根据预定义的坐标区域提取关键信息（如品牌、件号等），最终将结果保存到 CSV 文件中。

## 🚀 主要功能

- ✅ **实时监测**：自动监测 `refToPN` 目录，发现新图片立即处理。
- ✅ **精准提取**：基于 XML 配置的区域坐标，针对性地提取图片不同位置的文字。
- ✅ **智能处理**：自动匹配品牌编码、解析件号，并支持品牌及通用品牌的映射。
- ✅ **数据去重**：支持单张图片内去重及 CSV 文件全局一键去重。
- ✅ **多线程运行**：处理过程在后台线程执行，界面流畅不卡顿。
- ✅ **日志记录**：详细的识别日志展示，记录每个处理步骤。

## 🛠️ 技术栈

- **语言**: Python 3.8+
- **界面**: PySide6 (Qt for Python)
- **OCR 引擎**: [Umi-OCR](https://github.com/hiroi-sora/Umi-OCR) (通过 HTTP API 调用)
- **库**: `requests`, `Pillow`, `csv`, `xml.etree.ElementTree`

## 📦 安装与配置

### 1. 安装 Python 环境
确保已安装 Python 3.8 或更高版本。在项目根目录下，使用 pip 安装依赖：
```bash
pip install -r requirements.txt
```

### 2. 配置 Umi-OCR (必需)
本工具依赖 Umi-OCR 提供的 HTTP API。
1. 下载并运行 [Umi-OCR](https://github.com/hiroi-sora/Umi-OCR)。
2. 在 Umi-OCR 设置中开启 **HTTP 接口**。
3. 确保默认端口为 `1224`（如果修改了端口，请同步修改 `ocr_gui.py` 中的 `OCR_API_URL`）。

### 3. 文件结构说明
- `refToPN/`: **输入目录**，存放待处理的图片。
- `refToPartnum-OK/`: **输出目录**，处理完成后的图片会自动移动到此处。
- `refToPartnum.xml`: **区域配置文件**，通过 LabelImg 等工具生成的 XML，定义了 OCR 识别的坐标区域。
- `brandCode.csv`: **品牌映射表**，用于由于品牌编码转换为通用品牌。
- `ocr_results.csv`: **结果文件**，最终提取的数据将按行存入此文件。

## 📖 使用指南

1. **启动程序**：运行 `python ocr_gui.py`。
2. **准备图片**：将需要识别的图片放入 `refToPN` 文件夹。
3. **手动处理**：点击“处理当前图片”按钮进行单次批量处理。
4. **实时监测**：点击“开始监测”按钮，程序将进入监听状态，放入新图片会自动触发识别。
5. **去重维护**：如果 CSV 文件中存在重复记录，可点击“一键去重”按钮进行清理。

## ⚙️ 配置文件说明

### XML 区域配置 (`refToPartnum.xml`)
程序通过解析 XML 中的 `object` 节点来获取裁剪坐标。主要的识别区域包括：
- `主品牌编码`
- `转换码`
- `英文名称`
- `品牌编码`
- `品牌编码及件号` (用于智能提取 PN 对)

### 品牌映射 (`brandCode.csv`)
CSV 格式要求：
```csv
品牌编码,通用品牌
TOY,TOYOTA
HON,HONDA
...
```

## ⚠️ 注意事项

- **API 状态**：处理前请确保 Umi-OCR 软件处于开启状态。
- **配置一致性**：`refToPartnum.xml` 中的区域名称必须与代码中的逻辑匹配。
- **图片移动**：处理成功的图片会被移动到 `refToPartnum-OK` 目录，避免重复处理。
